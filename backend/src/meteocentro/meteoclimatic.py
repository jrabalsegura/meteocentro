"""Documented meteodata 0.1 feed; activation requires a recorded access decision."""

import re
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from meteocentro.aemet import Batch, check_status, digest, measurement
from meteocentro.catalog import external_identity, review_absences, upsert_source, validate_identity
from meteocentro.domain.eligibility import eligible_source_ids
from meteocentro.domain.observations import MetricKind, NormalizedObservation, convert
from meteocentro.domain.providers import Capabilities, ProviderAdapter, ProviderResult, ResultStatus
from meteocentro.ingestion import Ingestor
from meteocentro.ingestion_errors import IngestionError
from meteocentro.models import IngestionRun, Provider, StationSource

URL = "https://www.meteoclimatic.net/feed/xml/ES"
PREFIXES = ("ESMAD28", "ESCYL05", "ESCYL40", "ESCLM19")
UNITS = {"temperature": "C", "humidity": "%", "barometre": "hPa", "wind": "kmh", "rain": "mm"}
MAX_BYTES = 8_000_000
LICENSE_URL = "https://creativecommons.org/licenses/by-nc-nd/3.0/"
TERMS_URL = "https://www.meteoclimatic.net/index/wp/legal_es.html"


class NoDTD(ET.TreeBuilder):
    def doctype(self, *_):
        raise IngestionError("xml_dtd_forbidden")


def parse_xml(raw: bytes, *, fetched_at=None, max_bytes=MAX_BYTES):
    if len(raw) > max_bytes:
        raise IngestionError("response_too_large")
    declaration = re.search(rb"encoding=[\'\"]([^\'\"]+)", raw[:150], re.I)
    if b"\x00" in raw or (
        declaration
        and declaration[1].lower() not in (b"utf-8", b"iso-8859-1", b"iso-8859-15", b"us-ascii")
    ):
        raise IngestionError("unsupported_xml_encoding")
    try:
        root = ET.fromstring(raw, parser=ET.XMLParser(target=NoDTD()))
    except (ET.ParseError, ValueError):
        raise IngestionError("invalid_xml") from None
    if root.tag != "meteodata" or root.get("version") != "0.1" or root.find("stations") is None:
        raise IngestionError("xml_contract_changed", pause=True)
    records = []
    for station in root.findall("./stations/station"):
        values = {
            child.tag: (child.text or "").strip() for child in station if child.tag != "stationdata"
        }
        sensors = {}
        for sensor in station.findall("./stationdata/*"):
            if sensor.tag not in UNITS:
                continue
            if sensor.tag in sensors:
                raise IngestionError("xml_contract_changed", pause=True)
            fields = {child.tag: (child.text or "").strip() for child in sensor}
            if fields.get("unit") != UNITS[sensor.tag]:
                raise IngestionError("sensor_unit_changed", pause=True)
            sensors[sensor.tag] = fields
        values["sensors"] = sensors
        records.append(values)
    return Batch(
        records,
        [],
        digest({"format": "meteodata-0.1", "units": UNITS}),
        fetched_at or datetime.now(UTC),
    )


def station_time(row):
    try:
        stamp = parsedate_to_datetime(row.get("pubDate"))
        if stamp.utcoffset() is None:
            raise ValueError()
        return stamp.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("invalid_station_timestamp") from None


def normalize(row, batch):
    validate_identity("meteoclimatic", row["id"])
    instant = station_time(row)
    if instant > batch.fetched_at + timedelta(minutes=5):
        raise ValueError("future_observation")
    metrics = {}
    for sensor, field, name, unit, low, high, kind in (
        ("temperature", "now", "temperature", "°C", -100, 65, MetricKind.INSTANT),
        ("humidity", "now", "humidity", "%", 0, 100, MetricKind.INSTANT),
        ("wind", "now", "wind_speed", "m/s", 0, 540, MetricKind.INSTANT),
        ("wind", "azimuth", "wind_direction", "°", 0, 360, MetricKind.INSTANT),
        ("barometre", "now", "pressure_sea_level", "hPa", 850, 1100, MetricKind.SEA_LEVEL_PRESSURE),
        ("rain", "total", "rain_daily", "mm", 0, 2000, MetricKind.DAILY_COUNTER),
        ("temperature", "min", "temperature_daily_min", "°C", -100, 65, MetricKind.DAILY_MINIMUM),
        ("temperature", "max", "temperature_daily_max", "°C", -100, 65, MetricKind.DAILY_MAXIMUM),
        ("humidity", "min", "humidity_daily_min", "%", 0, 100, MetricKind.DAILY_MINIMUM),
        ("humidity", "max", "humidity_daily_max", "%", 0, 100, MetricKind.DAILY_MAXIMUM),
        (
            "barometre",
            "min",
            "pressure_sea_level_daily_min",
            "hPa",
            850,
            1100,
            MetricKind.DAILY_MINIMUM,
        ),
        (
            "barometre",
            "max",
            "pressure_sea_level_daily_max",
            "hPa",
            850,
            1100,
            MetricKind.DAILY_MAXIMUM,
        ),
        ("wind", "max", "wind_speed_daily_max", "m/s", 0, 540, MetricKind.DAILY_MAXIMUM),
    ):
        raw = row["sensors"].get(sensor, {}).get(field)
        # Only empty values mean missing. Unknown text is not silently mapped to zero.
        metric = measurement({field: raw or None}, field, unit, kind, low, high)
        metric.provider_quality = row.get("QOS") or None
        if kind in {MetricKind.DAILY_COUNTER, MetricKind.DAILY_MINIMUM, MetricKind.DAILY_MAXIMUM}:
            metric.period_basis = "provider_day_timezone_unknown"
        if name in {"wind_speed", "wind_speed_daily_max"}:
            metric.original_unit = "km/h"
            metric.value = convert(metric.value, "km/h", "m/s")
        metrics[name] = metric
    return NormalizedObservation(
        source_id=external_identity("meteoclimatic", row["id"]),
        product="meteoclimatic_current",
        observed_at=instant,
        fetched_at=batch.fetched_at,
        metrics=metrics,
        payload_hash=digest(
            {
                "id": row["id"],
                "pubDate": row["pubDate"],
                "QOS": row.get("QOS"),
                "sensors": row["sensors"],
            }
        ),
        normalizer_version="meteoclimatic-current-v2",
    )


def quality(row):
    return {
        "provider": "Meteoclimatic",
        "qos": row.get("QOS") or None,
        "reported_fields": row["sensors"],
        "daily_period": "provider_day_timezone_unknown",
        "pressure_reference": "sea_level_relative",
        "attribution": {
            "provider": "Meteoclimatic",
            "author": row.get("author") or None,
            "source_url": f"https://www.meteoclimatic.net/perfil/{row['id']}",
            "license_url": LICENSE_URL,
        },
    }


class MeteoclimaticAdapter(ProviderAdapter):
    capabilities = Capabilities(discover=True, current=True)

    def __init__(
        self, *, terms_reference=None, reserve=lambda: None, transport=None, max_bytes=MAX_BYTES
    ):
        self.terms_reference, self.reserve, self.max_bytes = terms_reference, reserve, max_bytes
        self.last_http_status = None
        self.http = httpx.Client(
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
            transport=transport,
            headers={
                "User-Agent": "Meteocentro/0.3",
                "Accept": "application/xml, text/xml;q=0.9, */*;q=0.1",
            },
        )

    def close(self):
        self.http.close()

    def probe(self):
        """One explicit, non-persisting technical diagnostic, never used by the scheduler."""
        return parse_xml(self.get_bytes(URL, self.max_bytes), max_bytes=self.max_bytes)

    def get_bytes(self, url, max_bytes, *, accept=None):
        self.reserve()
        try:
            started = time.monotonic()
            with self.http.stream(
                "GET", url, headers={"Accept": accept} if accept else None
            ) as response:
                self.last_http_status = response.status_code
                check_status(response.status_code, response.headers.get("Retry-After"))
                chunks, size = [], 0
                for chunk in response.iter_bytes(chunk_size=65536):
                    size += len(chunk)
                    if size > max_bytes:
                        raise IngestionError("response_too_large")
                    if time.monotonic() - started > 90:
                        raise IngestionError("download_deadline")
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.TimeoutException:
            raise IngestionError("http_timeout") from None
        except httpx.HTTPError:
            raise IngestionError("http_transport") from None

    def download(self, product="current"):
        if not self.terms_reference:
            raise IngestionError("pending_terms", pause=True)
        if product != "current":
            raise IngestionError("unsupported_product")
        return self.probe()

    def discover(self):
        if not self.terms_reference:
            return ProviderResult(ResultStatus.PENDING_TERMS, reason="pending_terms")
        return ProviderResult(ResultStatus.OK, data=self.download().records)

    def fetch_current(self):
        if not self.terms_reference:
            return ProviderResult(ResultStatus.PENDING_TERMS, reason="pending_terms")
        batch = self.download()
        return ProviderResult(
            ResultStatus.OK, data=[normalize(row, batch) for row in batch.records]
        )


class MeteoclimaticIngestor(Ingestor):
    def ingest(self, batch, *, after_chunk=None):
        counters = Counter(
            {
                key: 0
                for key in (
                    "valid",
                    "outside",
                    "excluded",
                    "invalid",
                    "inserted",
                    "revised",
                    "unchanged",
                    "new_sources",
                    "updated_sources",
                    "potential_duplicates",
                    "pending",
                    "location_review",
                    "duplicate_records",
                    "conflicting_records",
                    "absent_sources",
                    "absence_review",
                )
            }
        )
        # Identical duplicates are collapsed. Conflicting same-hour rows require review,
        # never an arbitrary last-wins correction from XML ordering.
        unique, conflicts = {}, set()
        for row in batch.records:
            key = (row.get("id"), row.get("pubDate"))
            if key in unique:
                counters["duplicate_records"] += 1
                if unique[key] != row:
                    conflicts.add(key)
            unique[key] = row
        watermarks = dict(self.claim.cursor.get("sources", {}))
        newest = None
        rows = sorted(unique.items(), key=lambda pair: str(pair[0]))
        for start in range(0, len(rows), 25):
            with Session(self.queue.engine) as db, db.begin():
                self.queue.fence(db, self.claim)
                provider = db.get(Provider, self.claim.provider_id)
                for key, row in rows[start : start + 25]:
                    if key in conflicts:
                        counters["conflicting_records"] += 1
                        continue
                    try:
                        validate_identity("meteoclimatic", row.get("id", ""))
                        if not row["id"].startswith(PREFIXES):
                            counters["outside"] += 1
                            continue
                        item = normalize(row, batch)
                        stored = db.scalar(
                            select(StationSource).where(
                                StationSource.provider_id == provider.id,
                                StationSource.external_id == row["id"],
                            )
                        )
                        metadata = stored.source_metadata if stored else {}
                        verified = metadata.get("location_evidence") and metadata.get("verified_at")
                        info = {
                            "external_id": row["id"],
                            "name": row.get("location") or row["id"],
                            "latitude": stored.latitude if verified else None,
                            "longitude": stored.longitude if verified else None,
                            "altitude_m": None,
                            "precision": metadata.get("precision"),
                            "qos": row.get("QOS"),
                        }
                    except (ValueError, TypeError, KeyError, OverflowError):
                        counters["invalid"] += 1
                        continue
                    source = upsert_source(db, provider, info, counters)
                    if source is None:
                        continue
                    if source.id not in db.scalars(eligible_source_ids(source.station_id)).all():
                        counters["excluded"] += 1
                        continue
                    self.store_observation(db, item, source, quality(row), counters)
                    stamp = item.observed_at.isoformat()
                    watermarks[row["id"]] = max(stamp, watermarks.get(row["id"], stamp))
                    newest = max(newest or stamp, stamp)
                    counters["valid"] += 1
                self.queue.fence(db, self.claim)
                db.get(IngestionRun, self.claim.run_id).result = dict(counters)
            if after_chunk:
                after_chunk()
        with Session(self.queue.engine) as db, db.begin():
            self.queue.fence(db, self.claim)
            review_absences(
                db,
                self.claim.provider_id,
                {row.get("id") for row in batch.records},
                batch.fetched_at,
                counters,
            )
            self.queue.fence(db, self.claim)
        return {
            **counters,
            "received": len(batch.records),
            "newest_observed_at": newest,
            "new_data": counters["inserted"] > 0,
            "metadata_hash": batch.metadata_hash,
        }, {"sources": watermarks, "last_confirmed_at": batch.fetched_at.isoformat()}

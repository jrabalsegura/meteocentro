"""AEMET's verified two-step products. No temporary URL or secret is persisted."""

import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from hashlib import sha256
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import httpx

from meteocentro.domain.observations import Measurement, MetricKind, NormalizedObservation
from meteocentro.domain.providers import Capabilities, ProviderAdapter, ProviderResult, ResultStatus
from meteocentro.ingestion_errors import IngestionError

BASE = "https://opendata.aemet.es/opendata/api/"
PATHS = {
    "current": "observacion/convencional/todas/",
    "inventory": "valores/climatologicos/inventarioestaciones/todasestaciones/",
}
NORMALIZER_VERSION = "aemet-current-v1"
MAX_BYTES = 16_000_000


def digest(value) -> str:
    return sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def source_identity(external_id):
    return uuid5(NAMESPACE_URL, "https://opendata.aemet.es/station/" + external_id)


def decode_json(raw: bytes):
    def reject_nonfinite(_):
        raise ValueError("nonfinite_json_number")

    try:
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = raw.decode("iso-8859-1")
        return json.loads(content, parse_constant=reject_nonfinite)
    except (ValueError, UnicodeError):
        raise IngestionError("invalid_json") from None


def checked_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.hostname != "opendata.aemet.es"
            or parts.port not in (None, 443)
            or parts.username
            or parts.password
            or parts.fragment
            or not parts.path.startswith("/opendata/")
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise IngestionError("unsafe_data_url", pause=True) from None
    return url


def retry_after(value: str | None) -> datetime:
    now = datetime.now(UTC)
    try:
        if value is not None and re.fullmatch(r"\d+", value.strip()):
            return now + timedelta(seconds=int(value))
        instant = parsedate_to_datetime(value)
        if instant.utcoffset() is None:
            instant = instant.replace(tzinfo=UTC)
        return max(now, instant)
    except (ValueError, TypeError, OverflowError):
        return now + timedelta(minutes=1)


def check_status(status: int, header: str | None = None):
    if status in (401, 403):
        raise IngestionError("invalid_credentials", pause=True)
    if status == 429:
        raise IngestionError("rate_limited", retry_at=retry_after(header))
    if status in (204, 404):
        raise IngestionError("product_unavailable")
    if status != 200:
        raise IngestionError("provider_transient" if status >= 500 else "unexpected_http")


def folded(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", value.lower()) if not unicodedata.combining(c)
    )


def validate_metadata(product: str, fields: list):
    """Check the semantic fields we consume, including units and measured periods."""
    if not isinstance(fields, list) or any(
        not isinstance(f, dict) or not isinstance(f.get("id"), str) for f in fields
    ):
        raise IngestionError("metadata_contract_changed", pause=True)
    by_id = {field.get("id"): folded(json.dumps(field, ensure_ascii=False)) for field in fields}
    if len(by_id) != len(fields):
        raise IngestionError("metadata_contract_changed", pause=True)
    expected = (
        {"idema", "lat", "lon", "fint", "ta", "hr", "vv", "prec", "pacutp", "pres", "pres_nmar"}
        if product == "current"
        else {"indicativo", "nombre", "latitud", "longitud", "altitud"}
    )
    if not expected <= by_id.keys():
        raise IngestionError("metadata_contract_changed", pause=True)
    if product == "current":
        checks = [
            "utc" in by_id["fint"],
            any(unit in by_id["ta"] for unit in ("ºc", "°c", "celsius"))
            and "instantanea" in by_id["ta"],
            "%" in by_id["hr"] and "instantanea" in by_id["hr"],
            "m/s" in by_id["vv"] and re.search(r"\b10 minutos\b", by_id["vv"]),
            all(
                "mm" in by_id[f] and re.search(r"\b60 minutos\b", by_id[f])
                for f in ("prec", "pacutp")
            ),
            "hpa" in by_id["pres"] and "barometro" in by_id["pres"],
            "hpa" in by_id["pres_nmar"] and "mar" in by_id["pres_nmar"],
        ]
        if not all(checks):
            raise IngestionError("metadata_contract_changed", pause=True)


@dataclass(frozen=True)
class Batch:
    records: list
    fields: list
    metadata_hash: str
    fetched_at: datetime


class AemetAdapter(ProviderAdapter):
    capabilities = Capabilities(discover=True, current=True)

    def __init__(
        self,
        key: str | None,
        *,
        reserve=lambda: None,
        get_metadata=lambda _: None,
        save_metadata=lambda *_: None,
        transport=None,
        max_bytes=MAX_BYTES,
    ):
        self.key = key
        self.reserve = reserve
        self.get_metadata = get_metadata
        self.save_metadata = save_metadata
        self.max_bytes = max_bytes
        self.http = httpx.Client(
            timeout=httpx.Timeout(30, connect=10, pool=10, write=10),
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "Meteocentro/0.2", "Accept": "application/json"},
        )

    def close(self):
        self.http.close()

    def request(self, url, *, authenticated=False):
        checked_url(url)
        self.reserve()
        # Only the API envelope receives the key; no query strings and no redirects.
        try:
            started = time.monotonic()
            with self.http.stream(
                "GET", url, headers={"api_key": self.key} if authenticated else {}
            ) as response:
                check_status(response.status_code, response.headers.get("Retry-After"))
                chunks, length = [], 0
                for chunk in response.iter_bytes(chunk_size=65536):
                    length += len(chunk)
                    if length > self.max_bytes:
                        raise IngestionError("response_too_large")
                    if time.monotonic() - started > 90:
                        raise IngestionError("download_deadline")
                    chunks.append(chunk)
                decoded = decode_json(b"".join(chunks))
                if authenticated and isinstance(decoded, dict):
                    try:
                        check_status(
                            int(decoded.get("estado", 0)), response.headers.get("Retry-After")
                        )
                    except (TypeError, ValueError):
                        raise IngestionError("invalid_envelope") from None
                return decoded
        except httpx.TimeoutException:
            raise IngestionError("http_timeout") from None
        except httpx.HTTPError:
            raise IngestionError("http_transport") from None

    def download(self, product: str) -> Batch:
        if not self.key:
            raise IngestionError("pending_access", pause=True)
        envelope = self.request(BASE + PATHS[product], authenticated=True)
        if not isinstance(envelope, dict):
            raise IngestionError("invalid_envelope")
        try:
            check_status(int(envelope.get("estado", 0)))
        except (ValueError, TypeError):
            raise IngestionError("invalid_envelope") from None
        # Validate both URLs even when using fresh cached metadata.
        data_url, metadata_url = (checked_url(envelope.get(k)) for k in ("datos", "metadatos"))
        fields = self.get_metadata(product)
        if fields is None:
            metadata = self.request(metadata_url)
            fields = metadata.get("campos") if isinstance(metadata, dict) else None
            validate_metadata(product, fields)
            self.save_metadata(product, fields)
        validate_metadata(product, fields)
        records = self.request(data_url)
        if not isinstance(records, list):
            raise IngestionError("invalid_records")
        return Batch(records, fields, digest(fields), datetime.now(UTC))

    def discover(self):
        return ProviderResult(status=ResultStatus.OK, data=self.download("inventory").records)

    def fetch_current(self):
        batch = self.download("current")
        return ProviderResult(
            status=ResultStatus.OK,
            data=[observation for row in batch.records for observation in normalize(row, batch)],
        )


def decimal_value(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("invalid_number")
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except InvalidOperation:
        raise ValueError("invalid_number") from None
    if not number.is_finite():
        raise ValueError("invalid_number")
    return number


def dms(value, *, latitude: bool) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("invalid_coordinate")
    match = re.fullmatch(r"(\d{2,3})(\d{2})(\d{2})([NSEW])", value)
    if not match:
        raise ValueError("invalid_coordinate")
    deg, minute, second = (int(match[i]) for i in (1, 2, 3))
    hemi = match[4]
    if minute >= 60 or second >= 60 or hemi not in ("NS" if latitude else "EW"):
        raise ValueError("invalid_coordinate")
    number = Decimal(deg) + Decimal(minute) / 60 + Decimal(second) / 3600
    if number > (90 if latitude else 180):
        raise ValueError("invalid_coordinate")
    return number * (-1 if hemi in "SW" else 1)


def station_record(row: dict, product: str) -> dict:
    if not isinstance(row, dict):
        raise ValueError("invalid_record")
    external_id = row.get("idema" if product == "current" else "indicativo")
    if not isinstance(external_id, str) or not re.fullmatch(r"[A-Za-z0-9]{1,20}", external_id):
        raise ValueError("invalid_identity")
    if product == "current":
        lat, lon = decimal_value(row.get("lat")), decimal_value(row.get("lon"))
    else:
        lat = dms(row.get("latitud"), latitude=True)
        lon = dms(row.get("longitud"), latitude=False)
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("invalid_coordinate")
    altitude = decimal_value(row.get("alt" if product == "current" else "altitud"))
    if altitude is not None and not -500 <= altitude <= 9000:
        raise ValueError("invalid_altitude")
    return {
        "external_id": external_id,
        "latitude": lat,
        "longitude": lon,
        "name": str(row.get("ubi") or row.get("nombre") or external_id)[:200],
        "altitude_m": altitude,
    }


def observation_time(row: dict) -> datetime:
    value = row.get("fint")
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?", value
    ):
        raise ValueError("invalid_timestamp")
    instant = datetime.fromisoformat(value)
    # AEMET metadata explicitly defines fint as UTC, including responses without suffix.
    return instant.replace(tzinfo=UTC) if instant.utcoffset() is None else instant.astimezone(UTC)


def measurement(row, field, unit, kind, low, high):
    value = decimal_value(row.get(field))
    flags = []
    usable = value
    if value is not None and not low <= value <= high:
        flags, usable = ["outside_physical_range"], None
    return Measurement(
        value=usable,
        unit=unit,
        kind=kind,
        original_value=value,
        original_unit=unit,
        plausibility_flags=flags,
    )


def normalize(row: dict, batch: Batch) -> list[NormalizedObservation]:
    info = station_record(row, "current")
    instant = observation_time(row)
    if instant > batch.fetched_at + timedelta(minutes=5):
        raise ValueError("future_observation")
    instant_metrics = {
        "temperature": measurement(row, "ta", "°C", MetricKind.INSTANT, -100, 65),
        "humidity": measurement(row, "hr", "%", MetricKind.INSTANT, 0, 100),
        "pressure_station": measurement(row, "pres", "hPa", MetricKind.STATION_PRESSURE, 300, 1100),
        "pressure_sea_level": measurement(
            row, "pres_nmar", "hPa", MetricKind.SEA_LEVEL_PRESSURE, 850, 1100
        ),
    }
    wind = measurement(row, "vv", "m/s", MetricKind.INTERVAL_MEAN, 0, 150)
    primary = measurement(row, "prec", "mm", MetricKind.INTERVAL_TOTAL, 0, 2000)
    alternate = measurement(row, "pacutp", "mm", MetricKind.INTERVAL_TOTAL, 0, 2000)
    # Keep the independent sensors for traceability; expose one canonical rain value.
    rain = primary if primary.value is not None else alternate
    groups = [(None, instant_metrics), (10, {"wind_speed": wind}), (60, {"rain": rain})]
    result = []
    for minutes, metrics in groups:
        result.append(
            NormalizedObservation(
                source_id=source_identity(info["external_id"]),
                product="aemet_current",
                observed_at=instant,
                fetched_at=batch.fetched_at,
                period_start=instant - timedelta(minutes=minutes) if minutes else None,
                period_end=instant if minutes else None,
                period_basis=f"preceding_{minutes}_minutes_UTC" if minutes else None,
                metrics=metrics,
                payload_hash=digest(row),
                normalizer_version=NORMALIZER_VERSION,
            )
        )
    return result

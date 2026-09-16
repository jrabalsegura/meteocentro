"""Bounded public profile metadata collection, on the ordinary worker's quota."""

import re
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from urllib.robotparser import RobotFileParser

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from meteocentro.catalog import (
    excluded,
    identity_lock,
    location,
    suggest_duplicates,
    upsert_source,
    validate_identity,
)
from meteocentro.ingestion_errors import IngestionError
from meteocentro.job_queue import db_now
from meteocentro.models import AuditEvent, IngestionRun, Provider, Station, StationSource

ROOT = "https://www.meteoclimatic.net"
PROFILE_TTL = timedelta(days=30)
BATCH_SIZE = 16


def has_manual_position(source):
    method = source.source_metadata.get("location_method")
    return source.latitude is not None and (
        method == "manual"
        or (source.capabilities.get("manual_registration") and method != "public_profile")
    )


class ProfileParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.heading, self.cells = [], []
        self.in_heading = self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "h1":
            self.in_heading = True
        if tag == "td":
            self.in_cell = "est_dades" in dict(attrs).get("class", "").split()
            if self.in_cell:
                self.cells.append("")

    def handle_endtag(self, tag):
        if tag == "h1":
            self.in_heading = False
        if tag == "td":
            self.in_cell = False

    def handle_data(self, data):
        if self.in_heading:
            self.heading.append(data)
        if self.in_cell:
            self.cells[-1] += data


def parse_profile(raw, external_id):
    validate_identity("meteoclimatic", external_id)
    parser = ProfileParser()
    # The public profile declares ISO-8859-15; accept explicit UTF-8 too.
    charset = re.search(rb"charset\s*=\s*[\"']?([\w-]+)", raw[:8000], re.I)
    encoding = charset[1].decode().lower() if charset else "iso-8859-15"
    if encoding not in {"iso-8859-15", "iso-8859-1", "utf-8"}:
        raise ValueError("profile_encoding")
    parser.feed(raw.decode(encoding))
    if not re.search(r"\(" + re.escape(external_id) + r"\)", "".join(parser.heading)):
        raise ValueError("profile_identity_mismatch")
    pattern = (
        r"(\d{1,2})[º°]\s*(\d{1,2})['′]\s*([NS])\s*"
        r"(\d{1,3})[º°]\s*(\d{1,2})['′]\s*([EW])\s*"
        r"-\s*(-?\d+(?:[.,]\d+)?)\s*m"
    )
    matches = [match for cell in parser.cells for match in re.findall(pattern, cell)]
    if len(matches) != 1:
        raise ValueError("profile_coordinates_missing_or_ambiguous")
    lat_d, lat_m, lat_h, lon_d, lon_m, lon_h, alt = matches[0]
    if int(lat_m) >= 60 or int(lon_m) >= 60:
        raise ValueError("profile_invalid_minutes")
    lat = (Decimal(lat_d) + Decimal(lat_m) / 60) * (-1 if lat_h == "S" else 1)
    lon = (Decimal(lon_d) + Decimal(lon_m) / 60) * (-1 if lon_h == "W" else 1)
    if abs(lat) > 90 or abs(lon) > 180:
        raise ValueError("profile_invalid_coordinates")
    return {"latitude": lat, "longitude": lon, "altitude_m": Decimal(alt.replace(",", "."))}


def lock_source(db, provider_id, external_id):
    db.execute(text("SELECT pg_advisory_xact_lock(746303001)"))
    identity_lock(db, provider_id, external_id)
    source = db.scalar(
        select(StationSource).where(
            StationSource.provider_id == provider_id, StationSource.external_id == external_id
        )
    )
    if source is None:
        return None, None
    station = db.scalar(select(Station).where(Station.id == source.station_id).with_for_update())
    if (
        excluded(db, source)
        or source.status != "enabled"
        or station.moderation_status == "excluded"
    ):
        return None, None
    return source, station


def apply_profile(db, provider, external_id, coordinates, counters):
    source, station = lock_source(db, provider.id, external_id)
    if source is None:
        counters["excluded"] += 1
        return
    metadata = source.source_metadata
    if has_manual_position(source):
        counters["manual_preserved"] += 1
        return
    now = db_now(db)
    evidence = f"{ROOT}/perfil/{external_id}"
    info = {
        **coordinates,
        "external_id": external_id,
        "name": metadata.get("provider_name") or external_id,
        "precision": "minute",
        "qos": metadata.get("provider_quality"),
    }
    province, reason = location(info)
    # Only the automatic missing-location decision can be resolved automatically.
    if source.review_reason == "missing_coordinates" and source.latitude is None:
        source.latitude, source.longitude = coordinates["latitude"], coordinates["longitude"]
        station.latitude, station.longitude = source.latitude, source.longitude
        station.altitude_m, station.province_code = coordinates["altitude_m"], province
        source.review_reason = reason
        station.moderation_status = "review" if reason else "active"
        suggest_duplicates(db, source, info, counters)
    else:
        # Existing positions are retained; relocations create an audited review proposal.
        upsert_source(db, provider, info, counters)
    source.source_metadata = {
        **source.source_metadata,
        "location_method": "public_profile",
        "precision": "minute",
        "location_evidence": evidence,
        "verified_at": now.isoformat(),
        "profile_checked_at": now.isoformat(),
        "profile_next_check_at": (now + PROFILE_TTL).isoformat(),
        "profile_error": None,
        "reported_location": {key: str(value) for key, value in coordinates.items()},
    }
    db.add(
        AuditEvent(
            action="profile_location_checked",
            target_type="source",
            target_id=source.id,
            details={"evidence": evidence, "precision": "minute", "reason": reason},
        )
    )
    counters["profiles_checked"] += 1
    counters["located" if station.moderation_status == "active" else "review"] += 1


class MeteoclimaticCatalog:
    def __init__(self, queue, claim, adapter):
        self.queue, self.claim, self.adapter = queue, claim, adapter

    def candidates(self):
        with Session(self.queue.engine) as db:
            now = db_now(db)
            rows = db.scalars(
                select(StationSource)
                .where(
                    StationSource.provider_id == self.claim.provider_id,
                    StationSource.status == "enabled",
                )
                .order_by(StationSource.first_seen_at, StationSource.external_id)
            ).all()
            ids = []
            for source in rows:
                meta = source.source_metadata
                if (
                    has_manual_position(source)
                    or excluded(db, source)
                    or db.get(Station, source.station_id).moderation_status == "excluded"
                ):
                    continue
                next_check = meta.get("profile_next_check_at")
                if not next_check or datetime.fromisoformat(next_check) <= now:
                    ids.append(source.external_id)
            return ids[:BATCH_SIZE]

    def robots(self):
        with Session(self.queue.engine) as db:
            cached = db.get(Provider, self.claim.provider_id).capabilities.get("profile_robots", {})
            fresh = cached.get("checked_at") and (
                db_now(db) - datetime.fromisoformat(cached["checked_at"]) < timedelta(days=1)
            )
        if not fresh:
            raw = self.adapter.get_bytes(f"{ROOT}/robots.txt", 65536, accept="text/plain")
            with Session(self.queue.engine) as db, db.begin():
                self.queue.fence(db, self.claim)
                cached = {
                    "text": raw.decode("utf-8", errors="replace"),
                    "checked_at": db_now(db).isoformat(),
                }
                provider = db.get(Provider, self.claim.provider_id)
                provider.capabilities = {**provider.capabilities, "profile_robots": cached}
        robots = RobotFileParser()
        robots.parse(cached["text"].splitlines())
        return robots

    def run(self, *, after_profile=None):
        counters = Counter()
        ids = self.candidates()
        if not ids:
            return {"profiles_checked": 0}, self.claim.cursor
        robots = self.robots()
        for external_id in ids:
            validate_identity("meteoclimatic", external_id)
            url = f"{ROOT}/perfil/{external_id}"
            if not robots.can_fetch("Meteocentro", url):
                counters["robots_disallowed"] += 1
                continue
            # Exclusions are checked both before HTTP and after it, under the same locks.
            with Session(self.queue.engine) as db, db.begin():
                self.queue.fence(db, self.claim)
                source, _ = lock_source(db, self.claim.provider_id, external_id)
                if source is None:
                    counters["excluded"] += 1
                    continue
            error = None
            try:
                raw = self.adapter.get_bytes(url, 1_000_000, accept="text/html")
                coordinates = parse_profile(raw, external_id)
            except ValueError as exc:
                error = str(exc)
            except IngestionError as exc:
                # HTTP denial/rate limits/transport use the worker's normal backoff.
                if exc.code == "product_unavailable":
                    error = "profile_unavailable"
                else:
                    raise
            if after_profile:
                after_profile()
            with Session(self.queue.engine) as db, db.begin():
                self.queue.fence(db, self.claim)
                if error:
                    source, _ = lock_source(db, self.claim.provider_id, external_id)
                    if source:
                        source.source_metadata = {
                            **source.source_metadata,
                            "profile_error": error,
                            "profile_next_check_at": (db_now(db) + timedelta(days=1)).isoformat(),
                        }
                    counters["invalid_profiles"] += 1
                else:
                    apply_profile(
                        db,
                        db.get(Provider, self.claim.provider_id),
                        external_id,
                        coordinates,
                        counters,
                    )
                db.get(IngestionRun, self.claim.run_id).result = dict(counters)
        return dict(counters), self.claim.cursor

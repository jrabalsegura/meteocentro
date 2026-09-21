"""Local, explicit catalog maintenance; never downloads from a provider."""

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from meteocentro.aemet import decimal_value
from meteocentro.catalog import (
    excluded,
    identity_lock,
    link_source,
    location,
    recheck_boundary_locations,
    recheck_duplicate_radius,
    suggest_duplicates,
    upsert_source,
    validate_identity,
)
from meteocentro.config import get_settings
from meteocentro.db import get_engine
from meteocentro.job_queue import Queue
from meteocentro.models import AuditEvent, IdentityExclusion, Provider, Station, StationSource


def register_manual(
    db,
    provider,
    external_id,
    *,
    name=None,
    latitude=None,
    longitude=None,
    precision="minute",
    evidence=None,
):
    validate_identity(provider.code, external_id)
    lat, lon = decimal_value(latitude), decimal_value(longitude)
    if (lat is None) != (lon is None) or (lat is not None and not evidence):
        raise ValueError("coordinate_pair_and_evidence_required")
    if precision not in {"minute", "exact"}:
        raise ValueError("invalid_coordinate_precision")
    metadata = {
        "location_method": "manual",
        "precision": precision,
        "location_evidence": evidence,
        "verified_at": datetime.now(UTC).isoformat() if evidence else None,
    }
    info = {
        "external_id": external_id,
        "name": (name or external_id)[:200],
        "latitude": lat,
        "longitude": lon,
        "altitude_m": None,
        "precision": precision,
        "location_metadata": metadata,
    }
    province, reason = location(info)
    counters = Counter()
    db.execute(text("SELECT pg_advisory_xact_lock(746303001)"))
    identity_lock(db, provider.id, external_id)
    if db.scalar(
        select(IdentityExclusion.id).where(
            IdentityExclusion.provider_id == provider.id,
            IdentityExclusion.external_id == external_id,
            IdentityExclusion.revoked_at.is_(None),
        )
    ):
        return {"excluded": 1}
    source = db.scalar(
        select(StationSource).where(
            StationSource.provider_id == provider.id, StationSource.external_id == external_id
        )
    )
    # Resolve a previously detected missing position only through this explicit operation.
    if source and source.review_reason in {"missing_coordinates", "uncertain_boundary"}:
        station = db.scalar(
            select(Station).where(Station.id == source.station_id).with_for_update()
        )
        if lat is not None and not excluded(db, source) and station.moderation_status != "excluded":
            source.latitude, source.longitude = lat, lon
            station.latitude, station.longitude, station.province_code = lat, lon, province
            source.source_metadata = {**source.source_metadata, **metadata}
            source.review_reason = reason
            station.moderation_status = "review" if reason else "active"
            suggest_duplicates(db, source, info, counters)
    upsert_source(db, provider, info, counters, capability="manual_registration")
    source = db.scalar(
        select(StationSource).where(
            StationSource.provider_id == provider.id, StationSource.external_id == external_id
        )
    )
    if source:
        db.add(
            AuditEvent(
                action="manual_registration",
                target_type="source",
                target_id=source.id,
                details={"evidence": evidence, "precision": precision},
            )
        )
    return dict(counters)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("--provider", required=True, choices=("aemet", "meteoclimatic"))
    add.add_argument("--id", required=True)
    add.add_argument("--name")
    add.add_argument("--latitude")
    add.add_argument("--longitude")
    add.add_argument("--precision", choices=("minute", "exact"), default="minute")
    add.add_argument("--evidence")
    link = sub.add_parser("link")
    link.add_argument("--source", type=UUID, required=True)
    link.add_argument("--station", type=UUID, required=True)
    link.add_argument("--evidence", required=True)
    for name in ("recheck-duplicates", "recheck-boundaries"):
        recheck = sub.add_parser(name)
        recheck.add_argument("--evidence", required=True)
        recheck.add_argument("--apply", action="store_true", help="commit; otherwise preview only")
    args = parser.parse_args()
    engine = get_engine()
    if args.command == "add":
        Queue(engine, get_settings(), args.provider).schedule()
    try:
        with Session(engine) as db, db.begin():
            if args.command == "add":
                provider = db.scalar(select(Provider).where(Provider.code == args.provider))
                result = register_manual(
                    db,
                    provider,
                    args.id,
                    name=args.name,
                    latitude=args.latitude,
                    longitude=args.longitude,
                    precision=args.precision,
                    evidence=args.evidence,
                )
            elif args.command == "link":
                link_source(db, args.source, args.station, args.evidence)
                result = {"linked": True}
            else:
                review = (
                    recheck_duplicate_radius
                    if args.command == "recheck-duplicates"
                    else recheck_boundary_locations
                )
                result = review(db, args.evidence)
                result["applied"] = args.apply
                if not args.apply:
                    db.rollback()
        print(json.dumps(result))
        return 0
    except ValueError:
        print(json.dumps({"status": "error", "code": "invalid_catalog_input"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

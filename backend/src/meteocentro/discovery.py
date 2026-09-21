"""Verify a known provider ID through its allowed catalog, in the ordinary queue."""

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from meteocentro.aemet import station_record
from meteocentro.catalog import upsert_source, validate_identity
from meteocentro.models import Provider, StationSource


def verify_identity(queue, claim, adapter):
    external_id = claim.cursor["external_id"]
    validate_identity(queue.provider_code, external_id)
    counters = Counter()
    # Current AEMET is not a complete historical inventory. Check both bounded products.
    products = ("current", "inventory") if queue.provider_code == "aemet" else ("current",)
    found = False
    for product in products:
        batch = adapter.download(product)
        for row in batch.records:
            if not isinstance(row, dict):
                counters["invalid"] += 1
                continue
            field = "idema" if product == "current" else "indicativo"
            if queue.provider_code == "meteoclimatic":
                field = "id"
            if row.get(field) != external_id:
                continue
            found = True
            with Session(queue.engine) as db, db.begin():
                job = queue.fence(db, claim)
                if queue.provider_code == "aemet":
                    info = station_record(row, product)
                else:
                    source = db.scalar(
                        select(StationSource).where(
                            StationSource.provider_id == claim.provider_id,
                            StationSource.external_id == external_id,
                        )
                    )
                    info = {
                        "external_id": external_id,
                        "name": row.get("name") or external_id,
                        "latitude": source.latitude if source else None,
                        "longitude": source.longitude if source else None,
                        "altitude_m": None,
                        "precision": "minute",
                    }
                upsert_source(
                    db,
                    db.get(Provider, claim.provider_id),
                    info,
                    counters,
                    capability="daily_history" if product == "inventory" else "current",
                )
                # Subsequent exclusion can now cancel this formerly unknown identity's job.
                source = db.scalar(
                    select(StationSource).where(
                        StationSource.provider_id == claim.provider_id,
                        StationSource.external_id == external_id,
                    )
                )
                if source:
                    job.source_id = source.id
                queue.fence(db, claim)
            break
    return {**counters, "complete": True, "found": found, "coverage_incomplete": True}, claim.cursor

"""Synthetic restore fixture, fed to an isolated image over stdin; never shipped."""

import os
from datetime import UTC, datetime

from meteocentro.administration import moderate
from meteocentro.auth import password_hash
from meteocentro.db import get_engine
from meteocentro.models import AdminUser, Observation, Provider, Station, StationSource
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

url = make_url(os.environ["DATABASE_URL"])
assert os.environ.get("PHASE7_SYNTHETIC_ONLY") == "yes"
assert url.host.startswith("meteocentro-phase7-")
with Session(get_engine()) as db, db.begin():
    user = AdminUser(
        username="fixture", password_hash=password_hash("Phase7 synthetic password only!")
    )
    provider = Provider(code="aemet", name="AEMET SYNTHETIC", status="verified", capabilities={})
    station = Station(
        name="SYNTHETIC phase 7",
        province_code="28",
        latitude=40.4,
        longitude=-3.7,
        moderation_status="active",
    )
    db.add_all([user, provider, station])
    db.flush()
    source = StationSource(
        station_id=station.id,
        provider_id=provider.id,
        external_id="PHASE7",
        status="enabled",
        capabilities={"current": True},
    )
    db.add(source)
    db.flush()
    db.add(
        Observation(
            source_id=source.id,
            product="synthetic",
            observed_at=datetime.now(UTC),
            fetched_at=datetime.now(UTC),
            metrics={
                "temperature": {
                    "value": 12.3,
                    "unit": "°C",
                    "kind": "instant",
                    "plausibility_flags": [],
                }
            },
            quality={},
            payload_hash="7" * 64,
            normalizer_version="synthetic-v1",
        )
    )
    db.flush()
    moderate(db, user.id, station_id=station.id, reason="Synthetic backup and restore")
print("Synthetic observation, exclusion, account and audit created.")

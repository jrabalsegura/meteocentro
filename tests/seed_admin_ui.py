"""Synthetic browser fixture; refuses any database not explicitly named *_ui_test."""

import os
from datetime import UTC, datetime
from uuid import UUID

from alembic import command
from alembic.config import Config
from meteocentro.auth import password_hash
from meteocentro.db import get_engine
from meteocentro.models import (
    AdminUser,
    LatestObservation,
    Observation,
    Provider,
    Station,
    StationSource,
)
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

url = os.environ["DATABASE_URL"]
assert make_url(url).database.endswith("_ui_test")
command.upgrade(Config("backend/alembic.ini"), "head")
station_id = UUID("00000000-0000-4000-8000-000000000006")
with Session(get_engine()) as db, db.begin():
    assert db.get(Station, station_id) is None, "Use a fresh UI test database"
    db.add(
        AdminUser(
            username="fixture-owner",
            password_hash=password_hash(os.environ["E2E_ADMIN_PASSWORD"]),
        )
    )
    provider = Provider(
        code="aemet",
        name="AEMET · SINTÉTICO",
        status="verified",
        capabilities={"stale_after_seconds": 5400},
    )
    station = Station(
        id=station_id,
        name="SINTÉTICA · administración",
        province_code="28",
        latitude=40.42437,
        longitude=-3.70765,
        moderation_status="active",
    )
    db.add_all([provider, station])
    db.flush()
    source = StationSource(
        provider_id=provider.id,
        station_id=station.id,
        external_id="SYN6",
        status="enabled",
        latitude=station.latitude,
        longitude=station.longitude,
        capabilities={"current": True},
    )
    db.add(source)
    db.flush()
    now = datetime.now(UTC)
    observation = Observation(
        source_id=source.id,
        product="synthetic",
        observed_at=now,
        fetched_at=now,
        metrics={
            "temperature": {
                "value": 20,
                "unit": "°C",
                "kind": "instant",
                "plausibility_flags": [],
            }
        },
        quality={},
        payload_hash="0" * 64,
        normalizer_version="synthetic-v1",
    )
    db.add(observation)
    db.flush()
    db.add(
        LatestObservation(
            source_id=source.id,
            metric="temperature",
            observation_id=observation.id,
            observed_at=now,
        )
    )
print("Synthetic admin fixture ready; no provider calls")

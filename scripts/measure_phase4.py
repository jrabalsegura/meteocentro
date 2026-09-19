"""Offline 2,000-station benchmark, only on an empty disposable *_test database.

Run after migrations; TEST_DATABASE_URL is required. --keep leaves synthetic rows
for local browser inspection. Never use a development or pilot database.
"""

import argparse
import json
import math
import os
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from meteocentro.api import app
from meteocentro.db import get_session
from meteocentro.models import (
    LatestObservation,
    Observation,
    Provider,
    Station,
    StationSource,
)
from sqlalchemy import create_engine, delete, func, insert, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    url = os.environ["TEST_DATABASE_URL"]
    assert make_url(url).database.endswith("_test"), (
        "Requires a disposable *_test database"
    )
    engine = create_engine(url)
    now = datetime.now(UTC)
    stations, sources, observations, latest = [], [], [], []
    provider_id = uuid.uuid4()
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Station)) == 0, (
            "Database must be empty"
        )
        provider = Provider(
            id=provider_id,
            code="aemet",
            name="AEMET · datos SINTÉTICOS de prueba",
            status="verified",
            capabilities={"stale_after_seconds": 5400},
        )
        db.add(provider)
        db.commit()
        for i in range(2000):
            station_id, source_id, observation_id = (
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
            )
            code, lon, lat = [
                ("28", -3.7, 40.4),
                ("05", -4.7, 40.65),
                ("40", -4.1, 41),
                ("19", -2.7, 40.9),
            ][i % 4]
            stations.append(
                {
                    "id": station_id,
                    "name": f"SINTÉTICA {i:04} · prueba de carga",
                    "province_code": code,
                    "longitude": lon + math.sin(i * 137.5) * 0.7,
                    "latitude": lat + math.cos(i * 137.5) * 0.45,
                    "altitude_m": 600 + i % 900,
                    "moderation_status": "active",
                }
            )
            sources.append(
                {
                    "id": source_id,
                    "station_id": station_id,
                    "provider_id": provider.id,
                    "external_id": f"SYN-{i}",
                    "status": "enabled",
                    "capabilities": {"current": True},
                }
            )
            observed = now - timedelta(minutes=10 if i % 9 else 180)
            metrics = {
                name: {"value": value, "unit": unit, "kind": "instant"}
                for name, value, unit in [
                    ("temperature", i % 45 - 5, "°C"),
                    ("humidity", i % 100, "%"),
                    ("wind_speed", i % 20, "m/s"),
                ]
            }
            observations.append(
                {
                    "id": observation_id,
                    "source_id": source_id,
                    "product": "synthetic_phase4",
                    "observed_at": observed,
                    "fetched_at": now,
                    "metrics": metrics,
                    "quality": {"synthetic": True},
                    "payload_hash": "0" * 64,
                    "normalizer_version": "fixture-phase4",
                }
            )
            latest.extend(
                {
                    "id": uuid.uuid4(),
                    "source_id": source_id,
                    "metric": name,
                    "observation_id": observation_id,
                    "observed_at": observed,
                }
                for name in metrics
            )
        db.execute(insert(Station), stations)
        db.execute(insert(StationSource), sources)
        db.execute(insert(Observation), observations)
        db.execute(insert(LatestObservation), latest)
        db.commit()
        app.dependency_overrides[get_session] = lambda: db
        client = TestClient(app)
        times = []
        for _ in range(6):
            start = time.perf_counter()
            response = client.get("/api/v1/map?limit=2000")
            times.append(round((time.perf_counter() - start) * 1000, 1))
            assert response.status_code == 200
            assert len(response.json()["items"]) == 2000
        evidence = {
            "population": 2000,
            "synthetic": True,
            "api_ms": times,
            "warm_median_ms": statistics.median(times[1:]),
            "response_bytes": len(response.content),
            "sql_queries": "1 joined statement (asserted in test_phase4)",
            "timestamp": now.isoformat(),
        }
        Path("runtime/phase4").mkdir(parents=True, exist_ok=True)
        Path("runtime/phase4/api-measurements.json").write_text(
            json.dumps(evidence, indent=2)
        )
        print(json.dumps(evidence, indent=2))
        if not args.keep:
            for model in [
                LatestObservation,
                Observation,
                StationSource,
                Station,
                Provider,
            ]:
                db.execute(delete(model))
            db.commit()
        app.dependency_overrides.clear()


if __name__ == "__main__":
    main()

"""Offline annual archive benchmark. Requires an EMPTY disposable *_test database.

Synthetic regular 10-minute samples; no provider traffic. --keep retains the fixture
for local browser QA. It must never point to a development/pilot database.
"""

import argparse
import json
import os
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from meteocentro.api import app
from meteocentro.db import get_session
from meteocentro.history import VERSION, channel_for
from meteocentro.models import Base, Observation, Provider, Station, StationSource


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=int, default=20)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    url = os.environ["TEST_DATABASE_URL"]
    os.environ["DATABASE_URL"] = url
    assert make_url(url).database.endswith("_test")
    assert 1 <= args.sources <= 100
    engine = create_engine(url, connect_args={"options": "-c timezone=UTC"})
    root = Path("runtime/phase5")
    root.mkdir(parents=True, exist_ok=True)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 1, tzinfo=UTC)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Station)) == 0, (
            "Database must be empty"
        )
        provider = Provider(
            code="aemet",
            name="SINTÉTICO · prueba de volumen",
            status="verified",
            capabilities={"native_cadence_seconds": 600},
        )
        db.add(provider)
        db.flush()
        sources = []
        for i in range(args.sources):
            station = Station(
                name=f"SINTÉTICA {i:02} · archivo de prueba",
                province_code="28",
                latitude=40.4 + i * 0.01,
                longitude=-3.7,
                moderation_status="active",
            )
            db.add(station)
            db.flush()
            source = StationSource(
                station_id=station.id,
                provider_id=provider.id,
                external_id=f"BENCH-{i}",
                status="enabled",
                capabilities={"current": True},
            )
            db.add(source)
            sources.append(source)
        db.commit()
        for month in range(1, 13):
            a = f"2025-{month:02}-01"
            b = f"2025-{month + 1:02}-01" if month < 12 else "2026-01-01"
            db.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS observations_2025{month:02} PARTITION OF observations FOR VALUES FROM ('{a} 00:00:00+00') TO ('{b} 00:00:00+00')"
                )
            )
        db.commit()
        began = time.perf_counter()
        # Bypass only dirty scheduling for a bulk synthetic fixture, in one transaction.
        db.execute(text("ALTER TABLE observations DISABLE TRIGGER observations_dirty"))
        db.execute(
            text("""INSERT INTO observations(id,source_id,product,observed_at,fetched_at,metrics,quality,payload_hash,normalizer_version)
          SELECT gen_random_uuid(), s.id, 'synthetic_benchmark', t, now(),
          jsonb_build_object('temperature',jsonb_build_object('value',round((15+12*sin(extract(epoch from t)/86400))::numeric,2),
              'kind','instant','unit','°C','plausibility_flags','[]'::jsonb)),
          '{"synthetic": true}'::jsonb,repeat('0',64),'fixture-v1'
          FROM station_sources s CROSS JOIN generate_series(:a, :b - interval '10 minutes',interval '10 minutes') t"""),
            {"a": start, "b": end},
        )
        db.execute(text("ALTER TABLE observations ENABLE TRIGGER observations_dirty"))
        db.commit()
        insert_seconds = round(time.perf_counter() - began, 2)
        observation = Observation(
            product="synthetic_benchmark", normalizer_version="fixture-v1"
        )
        channel, description = channel_for(
            observation, "temperature", {"kind": "instant", "unit": "°C"}
        )
        db.execute(
            text("""INSERT INTO hourly_aggregates(id,source_id,metric,channel,period_start,period_end,minimum,maximum,mean,sample_count,coverage,method,stats)
          SELECT gen_random_uuid(),source_id,'temperature',:channel,date_trunc('hour',observed_at),date_trunc('hour',observed_at)+interval '1 hour',
            min((metrics->'temperature'->>'value')::float),max((metrics->'temperature'->>'value')::float),avg((metrics->'temperature'->>'value')::float),count(*),1,CAST(:version AS varchar),
            CAST(:description AS jsonb)||jsonb_build_object('minimum',min((metrics->'temperature'->>'value')::float),'maximum',max((metrics->'temperature'->>'value')::float),
            'mean',avg((metrics->'temperature'->>'value')::float),'total',null,'coverage',1,'covered_seconds',3600,'aggregation_method',CAST(:version AS varchar))
          FROM observations GROUP BY source_id,date_trunc('hour',observed_at)"""),
            {
                "channel": channel,
                "version": VERSION,
                "description": json.dumps(description),
            },
        )
        db.execute(
            text("""INSERT INTO daily_summaries(id,source_id,product,period_start,period_end,period_basis,method,channel,coverage,metrics,fetched_at,provisional,provenance)
          SELECT gen_random_uuid(),source_id,'synthetic_benchmark',date_trunc('day',observed_at AT TIME ZONE 'Europe/Madrid') AT TIME ZONE 'Europe/Madrid',
            (date_trunc('day',observed_at AT TIME ZONE 'Europe/Madrid')+interval '1 day') AT TIME ZONE 'Europe/Madrid',
            'Europe/Madrid',CAST(:version AS varchar),:channel,least(1,count(*)*600.0/extract(epoch FROM (((date_trunc('day',observed_at AT TIME ZONE 'Europe/Madrid')+interval '1 day') AT TIME ZONE 'Europe/Madrid')-(date_trunc('day',observed_at AT TIME ZONE 'Europe/Madrid') AT TIME ZONE 'Europe/Madrid')))),jsonb_build_object('temperature',CAST(:description AS jsonb)||jsonb_build_object(
              'minimum',min((metrics->'temperature'->>'value')::float),'maximum',max((metrics->'temperature'->>'value')::float),
              'mean',avg((metrics->'temperature'->>'value')::float),'coverage',least(1,count(*)*600.0/extract(epoch FROM (((date_trunc('day',observed_at AT TIME ZONE 'Europe/Madrid')+interval '1 day') AT TIME ZONE 'Europe/Madrid')-(date_trunc('day',observed_at AT TIME ZONE 'Europe/Madrid') AT TIME ZONE 'Europe/Madrid')))),'covered_seconds',count(*)*600,
              'aggregation_method',CAST(:version AS varchar))),now(),false,'{"synthetic": true}'::jsonb
          FROM observations GROUP BY source_id,date_trunc('day',observed_at AT TIME ZONE 'Europe/Madrid')"""),
            {
                "channel": channel,
                "version": VERSION,
                "description": json.dumps(description),
            },
        )
        # Regular fixture weights use real UTC lengths, including DST boundaries.
        db.commit()
        for table in [
            "observations",
            "hourly_aggregates",
            "daily_summaries",
            "station_sources",
        ]:
            db.execute(text(f"ANALYZE {table}"))
        db.commit()
        source = sources[0]
        app.dependency_overrides[get_session] = lambda: db
        client = TestClient(app)
        timings = {}
        response_bytes = {}
        for endpoint, options in [
            ("series", {"resolution": "hour"}),
            ("daily", {"resolution": "month"}),
            ("records", {}),
        ]:
            elapsed = []
            for _ in range(5):
                tick = time.perf_counter()
                response = client.get(
                    f"/api/v1/stations/{source.station_id}/{endpoint}",
                    params={
                        "source": str(source.id),
                        "from": start.isoformat(),
                        "to": end.isoformat(),
                        **options,
                    },
                )
                elapsed.append(round((time.perf_counter() - tick) * 1000, 1))
                assert response.status_code == 200, response.text[:1000]
            timings[endpoint] = {
                "ms": elapsed,
                "warm_median_ms": statistics.median(elapsed[1:]),
            }
            response_bytes[endpoint] = len(response.content)
        plan = db.execute(
            text(
                "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT * FROM hourly_aggregates WHERE source_id=:source AND metric='temperature' AND period_start>=:a AND period_start<:b ORDER BY period_start,channel LIMIT 10001"
            ),
            {"source": source.id, "a": start, "b": end},
        ).scalar()
        sizes = (
            db.execute(
                text(
                    """SELECT sum(pg_relation_size(relid)) AS heap_bytes,sum(pg_indexes_size(relid)) AS index_bytes,sum(pg_total_relation_size(relid)) AS total_bytes FROM pg_partition_tree('observations') WHERE isleaf"""
                )
            )
            .mappings()
            .one()
        )
        avg_size = db.scalar(
            text(
                "SELECT avg(pg_column_size(o)) FROM (SELECT * FROM observations LIMIT 10000) o"
            )
        )
        evidence = {
            "synthetic": True,
            "sources": args.sources,
            "observations": db.scalar(select(func.count()).select_from(Observation)),
            "insert_seconds": insert_seconds,
            "average_row_bytes": float(avg_size),
            "storage": dict(sizes),
            "api": timings,
            "response_bytes": response_bytes,
            "annual_plan": plan,
            "station_id": str(source.station_id),
            "source_id": str(source.id),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        (root / "annual-benchmark.json").write_text(
            json.dumps(evidence, indent=2, default=str)
        )
        print(
            json.dumps(
                {k: v for k, v in evidence.items() if k != "annual_plan"},
                indent=2,
                default=str,
            )
        )
        if not args.keep:
            tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
            db.execute(text(f"TRUNCATE {tables} CASCADE"))
            db.commit()
        app.dependency_overrides.clear()


if __name__ == "__main__":
    main()

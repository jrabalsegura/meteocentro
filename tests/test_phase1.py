import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from meteocentro.api import app
from meteocentro.db import get_session
from meteocentro.domain.eligibility import eligible_station_ids
from meteocentro.domain.observations import (
    Measurement,
    MetricKind,
    NormalizedObservation,
    normalized_measurement,
)
from meteocentro.domain.providers import Capabilities, ProviderAdapter, ResultStatus
from meteocentro.domain.provinces import classify_province
from meteocentro.models import (
    Exclusion,
    Observation,
    Provider,
    Station,
    StationSource,
)
from meteocentro.schema import EXPECTED_REVISION
from pydantic import ValidationError
from shapely.geometry import Polygon
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def station_with_source(db: Session):
    provider = Provider(
        code="fixture", name="Fuente sintética", status="verified", capabilities={}
    )
    station = Station(
        name="Estación de prueba",
        province_code="28",
        latitude=Decimal("40.4168"),
        longitude=Decimal("-3.7038"),
        moderation_status="active",
    )
    db.add_all([provider, station])
    db.flush()
    source = StationSource(
        provider_id=provider.id,
        station_id=station.id,
        external_id="TEST0",
        status="enabled",
        capabilities={},
    )
    db.add(source)
    db.commit()
    return provider, station, source


def observation(
    source_id, observed_at, period_start=None, period_end=None, period_basis=None
):
    return Observation(
        source_id=source_id,
        product="synthetic",
        observed_at=observed_at,
        fetched_at=observed_at + timedelta(minutes=1),
        period_start=period_start,
        period_end=period_end,
        period_basis=period_basis,
        metrics={"temperature": {"value": "0", "unit": "°C", "kind": "instant"}},
        quality={},
        payload_hash="0" * 64,
        normalizer_version="fixture-v1",
    )


def client_for(db: Session):
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app)


def test_provider_external_identity_is_unique_in_postgres(db):
    provider, station, _ = station_with_source(db)
    db.add(
        StationSource(
            provider_id=provider.id,
            station_id=station.id,
            external_id="TEST0",
            status="enabled",
            capabilities={},
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_observation_key_rejects_duplicates_with_null_period(db):
    _, _, source = station_with_source(db)
    instant = datetime(2026, 9, 14, 10, tzinfo=UTC)
    db.add(observation(source.id, instant))
    db.flush()
    db.add(observation(source.id, instant))
    with pytest.raises(IntegrityError):
        db.flush()


def test_observation_period_and_dst_semantics(db):
    _, _, source = station_with_source(db)
    madrid = ZoneInfo("Europe/Madrid")
    first = datetime(2026, 10, 25, 2, 30, tzinfo=madrid, fold=0).astimezone(UTC)
    second = datetime(2026, 10, 25, 2, 30, tzinfo=madrid, fold=1).astimezone(UTC)
    assert second - first == timedelta(hours=1)
    db.add_all([observation(source.id, first), observation(source.id, second)])
    db.flush()
    period_end = first
    period_start = first - timedelta(hours=1)
    db.add(observation(source.id, first, period_start, period_end, "UTC_hour"))
    db.flush()
    db.add(observation(source.id, first, period_start, period_end, "UTC_hour"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_zero_null_invalid_and_metric_kinds():
    zero = normalized_measurement(Decimal(0), "km/h", "m/s", MetricKind.INSTANT)
    absent = normalized_measurement(None, "km/h", "m/s", MetricKind.INSTANT)
    assert zero.value == Decimal(0)
    assert absent.value is None
    with pytest.raises(ValidationError):
        Measurement(value="not-a-number", unit="mm", kind=MetricKind.DAILY_COUNTER)
    now = datetime.now(UTC)
    base = {
        "source_id": uuid.uuid4(),
        "product": "synthetic",
        "observed_at": now,
        "fetched_at": now,
        "payload_hash": "a" * 64,
        "normalizer_version": "v1",
    }
    with pytest.raises(ValidationError):
        NormalizedObservation(
            **base,
            metrics={
                "rain": Measurement(
                    value=Decimal(1), unit="mm", kind=MetricKind.INSTANT
                )
            },
        )
    with pytest.raises(ValidationError):
        NormalizedObservation(**base, period_start=now, metrics={})
    with pytest.raises(ValidationError):
        NormalizedObservation(**base, period_basis="UTC_hour", metrics={})
    with pytest.raises(ValidationError):
        NormalizedObservation(
            **{**base, "observed_at": now.replace(tzinfo=None)}, metrics={}
        )


def test_province_classification_uses_full_geometry_and_boundary_rule():
    assert classify_province(-3.7038, 40.4168) == "28"
    assert classify_province(-4.7009, 40.6564) == "05"
    assert classify_province(-4.1184, 40.9429) == "40"
    assert classify_province(-3.164, 40.633) == "19"
    assert classify_province(2.17, 41.39) is None
    adjacent = {
        "28": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
        "05": Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
    }
    assert classify_province(1, 0.5, adjacent) == "05"
    assert classify_province(0.99, 0.5, adjacent) == "28"
    assert classify_province(1.01, 0.5, adjacent) == "05"


def test_adapter_unsupported_is_not_empty_success():
    class NoProducts(ProviderAdapter):
        @property
        def capabilities(self):
            return Capabilities()

    adapter = NoProducts()
    result = adapter.fetch_history("TEST0", datetime.now(UTC), datetime.now(UTC))
    assert result.status == ResultStatus.UNSUPPORTED
    assert result.data is None


def test_station_exclusion_wins_after_other_session_commits(db, engine):
    _, station, _ = station_with_source(db)
    assert (
        db.scalar(select(Station.id).where(Station.id.in_(eligible_station_ids())))
        == station.id
    )
    with client_for(db) as client:
        assert client.get("/api/v1/stations").json()["total"] == 1
        with Session(engine) as other:
            other.add(Exclusion(station_id=station.id, reason="test"))
            other.commit()
        assert client.get("/api/v1/stations").json()["total"] == 0
        assert client.get(f"/api/v1/stations/{station.id}").status_code == 404
        assert client.get(f"/api/v1/stations/{station.id}/latest").status_code == 404
        assert (
            client.get(
                f"/api/v1/stations/{station.id}/observations",
                params={"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"},
            ).status_code
            == 404
        )


def test_source_exclusion_and_new_source_do_not_restore_station(db):
    provider, station, source = station_with_source(db)
    db.add(Exclusion(source_id=source.id))
    db.commit()
    with client_for(db) as client:
        assert client.get("/api/v1/stations").json()["total"] == 0
    db.add(Exclusion(station_id=station.id))
    db.add(
        StationSource(
            provider_id=provider.id,
            station_id=station.id,
            external_id="TEST1",
            status="enabled",
            capabilities={},
        )
    )
    db.commit()
    with client_for(db) as client:
        assert client.get("/api/v1/stations").json()["total"] == 0


def test_readiness_checks_database_revision(db):
    with client_for(db) as client:
        assert client.get("/health/live").status_code == 200
        assert (
            client.get("/health/ready").json()["schema_revision"] == EXPECTED_REVISION
        )
        db.execute(text("UPDATE alembic_version SET version_num = 'wrong'"))
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "schema_mismatch"
        db.rollback()


def test_public_routes_use_real_empty_queries_and_validate_range(db):
    _, station, _ = station_with_source(db)
    with client_for(db) as client:
        assert client.get("/api/v1/stations").json()["total"] == 1
        assert client.get(f"/api/v1/stations/{station.id}/latest").json()["items"] == []
        params = {"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"}
        assert (
            client.get(
                f"/api/v1/stations/{station.id}/observations", params=params
            ).json()["items"]
            == []
        )
        assert (
            client.get(
                f"/api/v1/stations/{station.id}/daily-summaries", params=params
            ).json()["items"]
            == []
        )
        params["start"] = "2026-09-01T00:00:00"
        assert (
            client.get(
                f"/api/v1/stations/{station.id}/observations", params=params
            ).status_code
            == 422
        )


def test_station_list_freshness_is_batched_per_page(db):
    from meteocentro.models import LatestObservation
    from sqlalchemy import event

    provider, _, _ = station_with_source(db)
    provider.capabilities = {"stale_after_seconds": 3600}
    now = datetime.now(UTC).replace(microsecond=0)
    expected = {}
    for index, (state, age, capabilities) in enumerate(
        [
            ("fresh", timedelta(minutes=5), {"current": True}),
            ("stale", timedelta(hours=3), {"current": True}),
            ("unknown", None, {"current": True}),
            ("historical_only", None, {"daily_history": True}),
        ]
    ):
        station = Station(
            name=f"Lote {index}", province_code="28", moderation_status="active"
        )
        db.add(station)
        db.flush()
        source = StationSource(
            provider_id=provider.id,
            station_id=station.id,
            external_id=f"BATCH{index}",
            status="enabled",
            capabilities=capabilities,
        )
        db.add(source)
        db.flush()
        if age is not None:
            row = observation(source.id, now - age)
            db.add(row)
            db.flush()
            db.add(
                LatestObservation(
                    source_id=source.id,
                    metric="temperature",
                    observation_id=row.id,
                    observed_at=row.observed_at,
                )
            )
        expected[str(station.id)] = state
    db.commit()
    statements = []

    def count(*_):
        statements.append(1)

    event.listen(db.get_bind(), "before_cursor_execute", count)
    try:
        with client_for(db) as client:
            items = client.get("/api/v1/stations", params={"limit": 100}).json()[
                "items"
            ]
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", count)
    states = {item["id"]: item["freshness"] for item in items}
    assert {key: states[key] for key in expected} == expected
    # Constant query count: page size must not multiply database round trips.
    assert len(statements) <= 6

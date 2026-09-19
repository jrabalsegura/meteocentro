"""Map contract checks against real PostgreSQL; no external network."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from meteocentro.api import app
from meteocentro.db import get_session
from meteocentro.models import (
    Exclusion,
    IdentityExclusion,
    LatestObservation,
    Observation,
    Provider,
    Station,
    StationSource,
)
from sqlalchemy import event
from sqlalchemy.orm import Session


@pytest.fixture
def catalog(db):
    providers = [
        Provider(
            code=code,
            name=code,
            status="verified",
            capabilities={"stale_after_seconds": seconds},
        )
        for code, seconds in [("aemet", 5400), ("meteoclimatic", 2700)]
    ]
    db.add_all(providers)
    db.flush()

    def add(
        name="Sintética",
        provider=0,
        station=None,
        value=0,
        minutes=10,
        metric="temperature",
        kind="instant",
        unit="°C",
        start=None,
        end=None,
        basis=None,
        flags=None,
    ):
        if station is None:
            station = Station(
                name=name,
                province_code="28",
                latitude=40.4,
                longitude=-3.7,
                moderation_status="active",
            )
            db.add(station)
            db.flush()
        source = StationSource(
            station_id=station.id,
            provider_id=providers[provider].id,
            external_id=str(uuid.uuid4()),
            status="enabled",
            capabilities={"current": True},
        )
        db.add(source)
        db.flush()
        observed = datetime.now(UTC) - timedelta(minutes=minutes)
        observation = Observation(
            source_id=source.id,
            product="synthetic",
            observed_at=observed,
            fetched_at=datetime.now(UTC),
            period_start=start,
            period_end=end,
            period_basis=basis,
            metrics={
                metric: {
                    "value": value,
                    "unit": unit,
                    "kind": kind,
                    "plausibility_flags": flags or [],
                }
            },
            quality={},
            payload_hash="0" * 64,
            normalizer_version="phase4-fixture",
        )
        db.add(observation)
        db.flush()
        db.add(
            LatestObservation(
                source_id=source.id,
                metric=metric,
                observation_id=observation.id,
                observed_at=observed,
            )
        )
        db.commit()
        return station, source, observation

    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app), add, providers


def test_map_zero_null_time_and_wind_conversion(catalog):
    client, add, _ = catalog
    station, _, original = add(value=0)
    add(name="Sin dato", value=None)
    response = client.get("/api/v1/map")
    assert response.headers["cache-control"] == "no-store"
    items = response.json()["items"]
    assert items[0]["reading"] is None
    assert items[1]["reading"]["value"] == 0
    assert items[1]["reading"]["observed_at"] == original.observed_at.isoformat()
    add(
        station=station, value=10, metric="wind_speed", unit="m/s", kind="interval_mean"
    )
    wind = client.get("/api/v1/map?metric=wind_speed").json()["items"][1]["reading"]
    assert wind["value"] == 36 and wind["unit"] == "km/h"


def test_stale_is_observation_time_not_fetch_time_and_never_extreme(catalog):
    client, add, _ = catalog
    add(name="Antigua", value=60, minutes=91)
    add(name="Actual cero", value=0, minutes=2)
    add(name="Actual diez", value=10, minutes=3)
    response = client.get("/api/v1/map").json()
    assert response["counts"] == {"stale": 1, "fresh": 2}
    assert response["extremes"]["maximum"]["reading"]["value"] == 10
    assert response["extremes"]["minimum"]["reading"]["value"] == 0
    assert len(client.get("/api/v1/map?freshness=stale").json()["items"]) == 1


def test_source_preference_fallback_and_filters(catalog):
    client, add, _ = catalog
    station, aemet, _ = add(value=7, minutes=120)
    _, meteo, _ = add(station=station, provider=1, value=9, minutes=2)
    result = client.get("/api/v1/map").json()["items"][0]
    assert result["reading"]["source_id"] == str(meteo.id)
    assert result["fallback"] is True
    assert len(result["sources"]) == 2
    filtered = client.get("/api/v1/map?network=aemet").json()["items"][0]
    assert filtered["reading"]["source_id"] == str(aemet.id)
    assert filtered["freshness"] == "stale"
    assert len(filtered["sources"]) == 1
    assert client.get("/api/v1/map?province=05").json()["items"] == []
    assert client.get("/api/v1/map?bbox=-4,40,-3,41").json()["total"] == 1
    assert client.get("/api/v1/map?q=" + meteo.external_id).json()["total"] == 1
    assert client.get("/api/v1/map?q=%25").json()["total"] == 0
    current = client.get(f"/api/v1/stations/{station.id}/current").json()
    assert len(current["readings"]) == 2


@pytest.mark.parametrize(
    "target", ["station", "source", "identity", "disabled", "review"]
)
def test_exclusions_across_all_public_views_after_other_commit(
    db, engine, catalog, target
):
    client, add, providers = catalog
    station, source, _ = add()
    assert client.get("/api/v1/map").json()["total"] == 1
    with Session(engine) as other:
        if target == "station":
            other.add(Exclusion(station_id=station.id))
        if target == "source":
            other.add(Exclusion(source_id=source.id))
        if target == "identity":
            other.add(
                IdentityExclusion(
                    provider_id=source.provider_id, external_id=source.external_id
                )
            )
        if target == "disabled":
            other.get(Provider, providers[0].id).status = "disabled"
        if target == "review":
            other.get(Station, station.id).moderation_status = "review"
        other.commit()
    assert client.get("/api/v1/map").json()["items"] == []
    assert client.get("/api/v1/map?q=" + source.external_id).json()["total"] == 0
    assert client.get("/api/v1/stations").json()["total"] == 0
    for suffix in ["", "/current", "/latest"]:
        assert client.get(f"/api/v1/stations/{station.id}{suffix}").status_code == 404


def test_source_exclusion_keeps_only_other_origin(db, catalog):
    client, add, _ = catalog
    station, source, _ = add(value=8)
    _, other, _ = add(station=station, provider=1, value=9)
    db.add(Exclusion(source_id=source.id))
    db.commit()
    result = client.get(f"/api/v1/stations/{station.id}/current").json()
    assert [s["id"] for s in result["sources"]] == [str(other.id)]
    assert all(v["source_id"] == str(other.id) for v in result["readings"])


def test_rain_periods_never_mixed_or_daily_substituted(catalog):
    client, add, _ = catalog
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    add(
        name="Hora A",
        metric="rain",
        value=0,
        unit="mm",
        kind="interval_total",
        start=end - timedelta(hours=1),
        end=end,
        basis="preceding_60_minutes_UTC",
    )
    add(
        name="Hora B",
        metric="rain",
        value=40,
        unit="mm",
        kind="interval_total",
        start=end - timedelta(hours=2),
        end=end - timedelta(hours=1),
        basis="preceding_60_minutes_UTC",
    )
    add(
        name="Contador",
        provider=1,
        metric="rain_daily",
        value=100,
        unit="mm",
        kind="daily_counter",
    )
    result = client.get("/api/v1/map?metric=rain").json()
    assert result["extremes"]["reason"] == "different_periods"
    assert result["extremes"]["maximum"] is None
    assert result["items"][0]["reading"] is None


@pytest.mark.parametrize(
    "query",
    [
        "bbox=nan,0,1,1",
        "bbox=-4,40,-5,41",
        "bbox=1,2,3",
        "bbox=0,0,1,91",
        "province=99",
        "network=unknown",
        "metric=rain_daily",
        "freshness=new",
        "limit=5001",
        "limit=0",
    ],
)
def test_invalid_map_parameters(catalog, query):
    client, _, _ = catalog
    assert client.get("/api/v1/map?" + query).status_code == 422


def test_limit_flags_do_not_advertise_partial_extremes(catalog):
    client, add, _ = catalog
    add(name="Uno", value=1)
    add(name="Dos", value=2)
    result = client.get("/api/v1/map?limit=1").json()
    assert result["truncated"] and result["total"] == 2
    assert result["extremes"]["reason"] == "limited_population"


def test_no_n_plus_one_and_future_or_invalid_not_recent(engine, catalog):
    client, add, _ = catalog
    add(name="Futura", minutes=-10, value=50)
    add(name="Inválida", flags=["out_of_range"], value=500)
    for i in range(10):
        add(name=f"Estación {i}")
    statements = []

    def record(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        result = client.get("/api/v1/map").json()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(statements) == 1
    assert result["extremes"]["eligible"] == 10
    assert result["counts"]["unknown"] == 2


def test_verified_municipality_search(db, catalog):
    client, add, _ = catalog
    _station, source, _ = add(name="Sensor norte")
    source.source_metadata = {"municipality": "Alcalá de Henares"}
    db.commit()
    result = client.get("/api/v1/map?q=Alcalá").json()
    assert result["total"] == 1
    assert result["items"][0]["municipality"] == "Alcalá de Henares"

"""Rain, DST, nulls, corrections, replay and public historical SQL on PostgreSQL."""

import copy
import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from meteocentro.aemet import AemetAdapter, Batch, digest
from meteocentro.api import app
from meteocentro.config import Settings
from meteocentro.db import get_session
from meteocentro.domain.observations import NormalizedObservation
from meteocentro.history import (
    aggregate,
    channels,
    civil_window,
    rebuild_day,
    refresh_aggregates,
)
from meteocentro.history_import import enqueue_history, normalize_daily
from meteocentro.history_maintenance import retention_preview, schedule_maintenance
from meteocentro.ingestion import Ingestor
from meteocentro.job_queue import Queue, db_now
from meteocentro.models import (
    AggregateDirtyDay,
    DailySummary,
    DailySummaryRevision,
    Exclusion,
    HourlyAggregate,
    Job,
    LatestObservation,
    Observation,
    ObservationRevision,
    Provider,
    ProviderRuntime,
    Station,
    StationSource,
)
from meteocentro.worker import run_claim
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

DAY = date(2026, 3, 29)
START, END = civil_window(DAY)


def sample(
    t,
    value,
    kind="instant",
    metric="temperature",
    start=None,
    end=None,
    basis=None,
    quality=None,
    flags=None,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        observed_at=t,
        period_start=start,
        period_end=end,
        period_basis=basis,
        product="synthetic",
        normalizer_version="fixture-v1",
        quality=quality or {},
        metrics={
            metric: {
                "value": value,
                "kind": kind,
                "unit": "mm" if metric.startswith("rain") else "°C",
                "plausibility_flags": flags or [],
            }
        },
    )


def stats(rows, start=START, end=END, cadence=3600):
    description, samples = next(iter(channels(rows).values()))
    return aggregate(samples, start, end, cadence, description)


@pytest.fixture
def archive(db, engine):
    queue = Queue(
        engine,
        Settings(
            database_url=str(engine.url), aemet_api_key="synthetic", _env_file=None
        ),
    )
    queue.schedule()
    provider = db.scalar(select(Provider).where(Provider.code == "aemet"))
    station = Station(
        name="Sintética históricos",
        moderation_status="active",
        province_code="28",
        latitude=40.4,
        longitude=-3.7,
    )
    db.add(station)
    db.flush()
    source = StationSource(
        provider_id=provider.id,
        station_id=station.id,
        external_id="SYN1",
        status="enabled",
        capabilities={"daily_history": True, "current": True},
    )
    db.add(source)
    db.commit()
    app.dependency_overrides[get_session] = lambda: db
    return station, source, queue, TestClient(app)


def store(db, source, rows):
    for row in rows:
        db.add(
            Observation(
                source_id=source.id,
                product=row.product,
                observed_at=row.observed_at,
                fetched_at=datetime.now(UTC),
                period_start=row.period_start,
                period_end=row.period_end,
                period_basis=row.period_basis,
                metrics=row.metrics,
                quality=row.quality,
                payload_hash=digest(row.metrics),
                normalizer_version=row.normalizer_version,
            )
        )
    db.commit()


def params(source, start=START, end=END, **extra):
    return {
        "source": str(source.id),
        "from": start.isoformat(),
        "to": end.isoformat(),
        **extra,
    }


@pytest.mark.parametrize(
    "day,hours", [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25)]
)
def test_dst_full_day_hourly_rain_sql(db, archive, day, hours):
    station, source, _, client = archive
    start, end = civil_window(day)
    assert (end - start).total_seconds() == hours * 3600
    rows = [
        sample(
            start + timedelta(hours=i + 1),
            1,
            "interval_total",
            "rain",
            start + timedelta(hours=i),
            start + timedelta(hours=i + 1),
            "preceding_60_minutes_UTC",
        )
        for i in range(hours)
    ]
    store(db, source, rows)
    rebuild_day(db, source, day, now=end + timedelta(days=1))
    db.commit()
    body = client.get(
        f"/api/v1/stations/{station.id}/daily",
        params=params(source, start, end, metric="rain"),
    ).json()
    assert len(body["items"]) == 1
    assert body["items"][0]["total"] == hours
    assert body["items"][0]["coverage"] == 1
    assert db.scalar(select(func.count()).select_from(HourlyAggregate)) == hours
    if hours == 25:
        assert (
            sum(
                row.period_start.astimezone(
                    __import__("zoneinfo").ZoneInfo("Europe/Madrid")
                ).hour
                == 2
                for row in db.scalars(select(HourlyAggregate))
            )
            == 2
        )


def test_counter_deltas_are_not_sum_and_unknown_base():
    rows = [
        sample(
            START + timedelta(hours=i),
            v,
            "daily_counter",
            "rain_daily",
            START,
            END,
            "UTC_day",
        )
        for i, v in enumerate([2, 2.4, 2.4, 3.1])
    ]
    result = stats(rows)
    assert result["total"] == pytest.approx(1.1)
    assert result["coverage"] == pytest.approx(3 / 23)
    assert "unknown_counter_base" in result["flags"]
    assert result["partial"]


def test_counter_reset_gap_and_correction_downward():
    rows = [
        sample(
            START + timedelta(hours=i),
            v,
            "daily_counter",
            "rain_daily",
            START,
            END,
            "UTC_day",
        )
        for i, v in enumerate([2, 2.4, 0.2, 0.5])
    ]
    result = stats(rows)
    assert result["total"] == pytest.approx(0.7)
    assert "counter_descent_or_null" in result["flags"]
    rows[2].period_start = START + timedelta(hours=2)
    rows[2].period_end = END + timedelta(days=1)
    rows[2].quality = {"counter_base_zero": True}
    assert "counter_period_reset" in stats(rows)["flags"]
    rows = [
        sample(START, 1, "daily_counter", "rain_daily", START, END, "UTC_day"),
        sample(
            START + timedelta(hours=5),
            5,
            "daily_counter",
            "rain_daily",
            START,
            END,
            "UTC_day",
        ),
    ]
    assert stats(rows)["total"] is None
    assert "counter_gap" in stats(rows)["flags"]


def test_unknown_daily_window_rolling_total_not_aggregated():
    assert stats([sample(START, 9, "daily_counter", "rain_daily")])["total"] is None
    assert stats([sample(START, 9, "rolling_total", "rain")])["total"] is None


def test_rain_overlap_cross_boundary_zero_and_null():
    rows = [
        sample(
            START + timedelta(hours=1),
            0,
            "interval_total",
            "rain",
            START,
            START + timedelta(hours=1),
            "hour",
        )
    ]
    assert stats(rows)["total"] == 0
    rows.append(
        sample(
            START + timedelta(minutes=90),
            9,
            "interval_total",
            "rain",
            START + timedelta(minutes=30),
            START + timedelta(minutes=90),
            "hour",
        )
    )
    assert stats(rows)["total"] is None
    assert "overlapping_intervals" in stats(rows)["flags"]
    rows = [
        sample(
            START,
            2,
            "interval_total",
            "rain",
            START - timedelta(minutes=30),
            START + timedelta(minutes=30),
            "hour",
        )
    ]
    assert stats(rows)["total"] is None
    assert "cross_boundary_total" in stats(rows)["flags"]
    assert stats([sample(START, None)])["mean"] is None


def test_irregular_time_weighting_null_stops_hold_and_peak():
    rows = [
        sample(START, 0),
        sample(START + timedelta(minutes=15), 40),
        sample(START + timedelta(minutes=30), None),
        sample(START + timedelta(hours=4), 0),
    ]
    result = stats(rows)
    assert result["mean"] == pytest.approx(40 * 900 / 5400)
    assert result["maximum"] == 40
    assert result["covered_seconds"] == 5400
    rows[1].metrics["temperature"]["plausibility_flags"] = ["outside_physical_range"]
    assert stats(rows)["maximum"] == 0


def test_vector_direction_handles_north_calm_and_opposition():
    rows = [
        sample(START, 350, metric="wind_direction"),
        sample(START + timedelta(hours=1), 10, metric="wind_direction"),
    ]
    for row in rows:
        row.metrics["wind_speed"] = {"value": 1}
    result = stats(rows)
    assert min(abs(result["mean"]), abs(result["mean"] - 360)) < 1e-8
    rows[1].metrics["wind_speed"]["value"] = 0
    assert stats(rows)["mean"] == pytest.approx(350)
    rows[0].metrics["wind_direction"]["value"] = 0
    rows[1].metrics["wind_direction"]["value"] = 180
    rows[1].metrics["wind_speed"]["value"] = 1
    assert stats(rows)["mean"] is None


def test_raw_null_gap_bounds_pagination_and_no_mixed_origins(db, archive):
    station, source, _, client = archive
    store(
        db,
        source,
        [
            sample(START, 0),
            sample(START + timedelta(hours=1), None),
            sample(START + timedelta(hours=5), 8),
        ],
    )
    path = f"/api/v1/stations/{station.id}/series"
    body = client.get(path, params=params(source)).json()
    assert [r["value"] for r in body["items"]] == [0, None, 8]
    assert body["items"][-1]["break_before"]
    response = client.get(path, params=params(source, limit=2))
    assert response.json()["next_offset"] == 2
    assert len(client.get(path, params=params(source, offset=2)).json()["items"]) == 1
    assert client.get(path, params=params(source, metric="invalid")).status_code == 422
    assert (
        client.get(
            path, params=params(source, end=END + timedelta(days=33))
        ).status_code
        == 422
    )
    assert (
        client.get(path, params={**params(source), "from": "2026-01-01"}).status_code
        == 422
    )
    second = StationSource(
        station_id=station.id,
        provider_id=source.provider_id,
        external_id="SYN2",
        status="enabled",
    )
    db.add(second)
    db.commit()
    assert (
        client.get(
            path, params={k: v for k, v in params(source).items() if k != "source"}
        ).status_code
        == 422
    )
    assert client.get(path, params=params(source)).status_code == 200


def test_correction_regenerates_dirty_days_and_extremes(db, engine, archive):
    station, source, queue, client = archive
    store(
        db,
        source,
        [sample(START + timedelta(hours=i), 10 if i != 8 else 40) for i in range(23)],
    )
    refresh_aggregates(engine, limit=10)
    db.expire_all()
    before = client.get(
        f"/api/v1/stations/{station.id}/records", params={"source": str(source.id)}
    ).json()
    assert before["items"][0]["maximum"]["value"] == 40
    row = db.scalar(
        select(Observation).where(Observation.observed_at == START + timedelta(hours=8))
    )
    claim = queue.claim("current")
    ingestor = Ingestor(queue, claim)
    normalized = NormalizedObservation(
        source_id=source.id,
        product=row.product,
        observed_at=row.observed_at,
        fetched_at=datetime.now(UTC),
        metrics={"temperature": {"value": 5, "unit": "°C", "kind": "instant"}},
        payload_hash="f" * 64,
        normalizer_version="fixture-v1",
    )
    counter = Counter()
    ingestor.store_observation(db, normalized, source, {}, counter)
    db.commit()
    assert counter["revised"] == 1
    assert db.scalar(select(func.count()).select_from(ObservationRevision)) == 1
    assert db.scalar(select(func.count()).select_from(AggregateDirtyDay)) > 0
    refresh_aggregates(engine, limit=10)
    db.expire_all()
    body = client.get(
        f"/api/v1/stations/{station.id}/daily", params=params(source)
    ).json()
    assert body["items"][0]["maximum"] == 10
    assert body["items"][0]["minimum"] == 5
    first = copy.deepcopy(body["items"][0])
    rebuild_day(db, source, DAY)
    db.commit()
    second = client.get(
        f"/api/v1/stations/{station.id}/daily", params=params(source)
    ).json()["items"][0]
    assert first["mean"] == second["mean"]
    assert db.scalar(select(func.count()).select_from(HourlyAggregate)) == 23


def test_exclusion_filters_every_history_output_including_cached_aggregates(
    db, archive
):
    station, source, _, client = archive
    store(db, source, [sample(START, 1)])
    rebuild_day(db, source, DAY)
    db.commit()
    with Session(db.bind) as other:
        other.add(Exclusion(source_id=source.id))
        other.commit()
    for endpoint in ["series", "daily", "records", "export.csv"]:
        assert (
            client.get(
                f"/api/v1/stations/{station.id}/{endpoint}", params=params(source)
            ).status_code
            == 404
        )
    assert client.get("/api/v1/daily", params={"day": str(DAY)}).json()["items"] == []


def test_csv_permissions_formula_injection_and_limits(db, archive):
    station, source, _, client = archive
    source.external_id = " =HYPERLINK(1)"
    db.commit()
    store(db, source, [sample(START, 0)])
    response = client.get(
        f"/api/v1/stations/{station.id}/export.csv", params=params(source)
    )
    assert response.status_code == 200
    assert "' =HYPERLINK(1)" in response.text
    assert response.headers["cache-control"] == "no-store"
    db.get(Provider, source.provider_id).code = "meteoclimatic"
    db.commit()
    assert (
        client.get(
            f"/api/v1/stations/{station.id}/export.csv", params=params(source)
        ).status_code
        == 403
    )


FIELDS = [
    {"id": k, "descripcion": v}
    for k, v in {
        "fecha": "fecha",
        "indicativo": "ID",
        "tmin": "°C",
        "tmax": "°C",
        "tmed": "°C",
        "prec": "mm 07 a 07",
        "horatmin": "UTC",
        "horatmax": "UTC",
        "racha": "m/s",
        "velmedia": "m/s",
        "presmax": "hPa estacion",
        "presmin": "hPa estacion",
    }.items()
]
DAILY = {
    "indicativo": "SYN1",
    "fecha": "2026-03-29",
    "tmin": "1,0",
    "tmax": "20,0",
    "tmed": "10,5",
    "prec": "Ip",
    "horatmax": "14:30",
    "racha": "8,0",
}


def daily_transport(rows, hook=None, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(str(request.url))
        if "/api/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "estado": 200,
                    "datos": "https://opendata.aemet.es/opendata/test/data",
                    "metadatos": "https://opendata.aemet.es/opendata/test/meta",
                },
            )
        if request.url.path.endswith("meta"):
            return httpx.Response(200, json={"campos": FIELDS})
        if hook:
            hook()
        return httpx.Response(200, json=rows)

    return httpx.MockTransport(handler)


def test_import_replay_resume_downward_correction_and_latest_unchanged(db, archive):
    station, source, queue, client = archive
    store(db, source, [sample(END + timedelta(days=30), 17)])
    current = db.scalar(select(Observation))
    db.add(
        LatestObservation(
            source_id=source.id,
            metric="temperature",
            observation_id=current.id,
            observed_at=current.observed_at,
        )
    )
    db.commit()
    assert enqueue_history(db, source.id, DAY, DAY + timedelta(days=30)) == 1
    assert enqueue_history(db, source.id, DAY, DAY + timedelta(days=30)) == 0
    assert (
        enqueue_history(db, source.id, DAY - timedelta(days=1), DAY + timedelta(days=1))
        == 1
    )
    db.commit()
    claim = queue.claim("history")
    assert claim
    # Pick the target window deterministically (earlier next_run_at).
    calls = []
    factory = lambda key, **kw: AemetAdapter(
        key, transport=daily_transport([DAILY], calls=calls), **kw
    )

    def crash():
        raise RuntimeError("after commit before acknowledgement")

    with pytest.raises(RuntimeError):
        run_claim(queue, claim, adapter_factory=factory, after_chunk=crash)
    db.expire_all()
    assert db.get(Job, claim.job_id).cursor["confirmed"]
    db.execute(
        update(Job)
        .where(Job.id == claim.job_id)
        .values(lease_until=db_now(db) - timedelta(seconds=1))
    )
    # Keep the second window from taking priority over the interrupted job.
    db.execute(
        update(Job)
        .where(Job.kind == "history", Job.id != claim.job_id)
        .values(next_run_at=db_now(db) + timedelta(days=1))
    )
    db.commit()
    resumed = queue.claim("history")
    assert resumed.job_id == claim.job_id
    assert run_claim(queue, resumed, adapter_factory=factory)["result"][
        "resumed_after_commit"
    ]
    assert len(calls) == 3
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(DailySummary)) == 6
    job = db.get(Job, claim.job_id)
    job.cursor = {"from": str(DAY), "to": str(DAY + timedelta(days=30))}
    job.status = "pending"
    job.next_run_at = db_now(db)
    db.commit()
    replay = queue.claim("history")
    assert replay is not None
    assert run_claim(queue, replay, adapter_factory=factory)["result"]["unchanged"] == 6
    db.expire_all()
    job = db.get(Job, claim.job_id)
    job.cursor = {"from": str(DAY), "to": str(DAY + timedelta(days=30))}
    job.status = "pending"
    job.next_run_at = db_now(db)
    db.commit()
    corrected = {**DAILY, "tmax": "18,0"}
    factory = lambda key, **kw: AemetAdapter(
        key, transport=daily_transport([corrected]), **kw
    )
    correction = queue.claim("history")
    assert correction is not None
    assert (
        run_claim(queue, correction, adapter_factory=factory)["result"]["revised"] == 6
    )
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(DailySummaryRevision)) == 6
    assert db.scalar(select(LatestObservation.observed_at)) == current.observed_at
    body = client.get(
        f"/api/v1/stations/{station.id}/series", params=params(source)
    ).json()
    assert body["items"] == []  # Diaries never become an old intraday curve.
    assert body["availability"]["day"]["first"] is not None
    data = client.get(
        f"/api/v1/stations/{station.id}/daily", params=params(source, metric="rain")
    ).json()["items"][0]
    assert data["total"] is None and "trace_below_0.1" in data["flags"]
    assert data["period_basis"] == "AEMET_07_07_UTC"


def test_import_excluded_during_download_discards_rows(db, archive):
    _, source, queue, _ = archive
    enqueue_history(db, source.id, DAY, DAY + timedelta(days=1))
    db.commit()

    def exclude():
        with Session(db.bind) as other:
            other.add(Exclusion(source_id=source.id))
            other.commit()

    factory = lambda key, **kw: AemetAdapter(
        key, transport=daily_transport([DAILY], hook=exclude), **kw
    )
    assert (
        run_claim(queue, queue.claim("history"), adapter_factory=factory)["result"][
            "excluded"
        ]
        == 1
    )
    assert db.scalar(select(func.count()).select_from(DailySummary)) == 0


def test_import_budget_reserved_and_unsupported_network(db, archive):
    _, source, queue, _ = archive
    enqueue_history(db, source.id, DAY, DAY + timedelta(days=1))
    db.commit()
    claim = queue.claim("history")
    with Session(db.bind) as other:
        state = other.get(ProviderRuntime, claim.provider_id)
        state.history_calls = queue.settings.history_daily_http_budget
        other.commit()
    report = run_claim(
        queue,
        claim,
        adapter_factory=lambda key, **kw: AemetAdapter(
            key, transport=daily_transport([DAILY]), **kw
        ),
    )
    assert report["code"] == "history_budget"
    current = queue.claim("current")
    assert current
    queue.reserve_http(current)
    db.get(Provider, source.provider_id).code = "meteoclimatic"
    db.commit()
    with pytest.raises(ValueError, match="pending_access_or_terms"):
        enqueue_history(db, source.id, DAY, DAY + timedelta(days=1))


def test_provider_trace_accum_null_and_original_window():
    adapter = AemetAdapter("synthetic", transport=daily_transport([DAILY]))
    result = adapter.fetch_daily_history("SYN1", DAY, DAY + timedelta(days=1))
    adapter.close()
    assert len(result.data) == 6 and result.status == "ok"
    assert all(item["method"] == "provider" for item in result.data)
    batch = Batch([DAILY], FIELDS, digest(FIELDS), datetime.now(UTC))
    for raw in ["Ip", "Acum", None, "0,0"]:
        rows = normalize_daily(
            {**DAILY, "prec": raw}, batch, "SYN1", DAY, DAY + timedelta(days=1)
        )
        rain = next(r for r in rows if "rain" in r["metrics"])
        assert rain["period_start"].hour == 7
        assert rain["metrics"]["rain"]["total"] == (0 if raw == "0,0" else None)
        assert rain["metrics"]["rain"]["original"]["prec"] == raw


def test_partition_uniqueness_future_maintenance_and_retention_dry_run(
    db, engine, archive
):
    _, source, _, _ = archive
    row = sample(START, 1)
    store(db, source, [row])
    with pytest.raises(IntegrityError):
        store(db, source, [row])
    db.rollback()
    assert (
        db.scalar(
            text("SELECT relkind FROM pg_class WHERE oid='observations'::regclass")
        )
        == "p"
    )
    schedule_maintenance(engine)
    schedule_maintenance(engine)
    assert (
        db.scalar(
            select(func.count()).select_from(Job).where(Job.kind == "aggregate_refresh")
        )
        == 1
    )
    result = retention_preview(db, 1, now=END + timedelta(days=90))
    assert result["purge_enabled"] is False
    assert result["providers"][0]["candidate_rows"] == 1
    assert result["providers"][0]["unverified_rows"] == 1
    assert db.scalar(select(func.count()).select_from(Observation)) == 1


def test_year_rollup_null_months_and_network_cutoff_separation(db, archive):
    station, source, _, client = archive
    store(db, source, [sample(START + timedelta(hours=i), i) for i in range(23)])
    rebuild_day(db, source, DAY, now=END + timedelta(days=1))
    db.commit()
    body = client.get(
        f"/api/v1/stations/{station.id}/daily",
        params=params(
            source,
            START - timedelta(days=90),
            END + timedelta(days=180),
            resolution="year",
        ),
    ).json()
    assert len(body["items"]) == 1
    assert body["items"][0]["coverage"] < 0.01
    assert body["items"][0]["total"] is None
    db.execute(delete(AggregateDirtyDay))
    db.commit()
    network = client.get("/api/v1/daily", params={"day": str(DAY)}).json()
    assert network["items"][0]["eligible"]
    assert network["extremes"][1]["extreme"]["maximum"] == 22


def test_rain_correction_to_zero_and_null_recalculates_total(db, engine, archive):
    station, source, _, client = archive
    store(
        db,
        source,
        [
            sample(
                START + timedelta(hours=i + 1),
                v,
                "interval_total",
                "rain",
                START + timedelta(hours=i),
                START + timedelta(hours=i + 1),
                "hour",
            )
            for i, v in enumerate([2, 1])
        ],
    )
    refresh_aggregates(engine, limit=8)
    assert (
        client.get(
            f"/api/v1/stations/{station.id}/daily", params=params(source, metric="rain")
        ).json()["items"][0]["total"]
        == 3
    )
    row = db.scalar(select(Observation).order_by(Observation.observed_at))
    row.metrics = {"rain": {**row.metrics["rain"], "value": 0}}
    db.commit()
    # A dirty aggregate must not contribute to network comparisons.
    assert (
        client.get("/api/v1/daily", params={"day": str(DAY), "metric": "rain"}).json()[
            "extremes"
        ]
        == []
    )
    refresh_aggregates(engine, limit=8)
    db.expire_all()
    item = client.get(
        f"/api/v1/stations/{station.id}/daily", params=params(source, metric="rain")
    ).json()["items"][0]
    assert item["total"] == 1
    assert item["coverage"] == pytest.approx(2 / 23)
    row.metrics = {"rain": {**row.metrics["rain"], "value": None}}
    db.commit()
    refresh_aggregates(engine, limit=8)
    db.expire_all()
    item = client.get(
        f"/api/v1/stations/{station.id}/daily", params=params(source, metric="rain")
    ).json()["items"][0]
    assert item["total"] == 1
    assert item["coverage"] == pytest.approx(1 / 23)


def test_incompatible_interval_lengths_and_normalizers_are_separate_channels():
    row = sample(
        START + timedelta(hours=1),
        1,
        "interval_total",
        "rain",
        START,
        START + timedelta(hours=1),
        "UTC",
    )
    short = sample(
        START + timedelta(minutes=10),
        1,
        "interval_total",
        "rain",
        START,
        START + timedelta(minutes=10),
        "UTC",
    )
    corrected = copy.deepcopy(row)
    corrected.normalizer_version = "fixture-v2"
    assert len(channels([row, short, corrected])) == 3


def test_import_unavailable_is_visible_and_does_not_retry_forever(db, archive):
    _, source, queue, _ = archive
    enqueue_history(db, source.id, DAY, DAY + timedelta(days=1))
    db.commit()
    claim = queue.claim("history")
    factory = lambda key, **kw: AemetAdapter(
        key, transport=httpx.MockTransport(lambda request: httpx.Response(404)), **kw
    )
    report = run_claim(queue, claim, adapter_factory=factory)
    assert report["result"]["availability"] == "unavailable"
    db.expire_all()
    assert db.get(Job, claim.job_id).status == "completed"
    assert db.get(Job, claim.job_id).cursor["availability"] == "unavailable"
    assert db.scalar(select(func.count()).select_from(DailySummary)) == 0


def test_future_partition_and_composite_fk(db, archive):
    from meteocentro.history_maintenance import ensure_partitions

    _, source, _, _ = archive
    future = datetime(2028, 2, 1, tzinfo=UTC)
    ensure_partitions(db, future)
    db.commit()
    store(db, source, [sample(future, 1)])
    assert (
        db.scalar(
            text(
                "SELECT tableoid::regclass::text FROM observations WHERE observed_at=:at"
            ),
            {"at": future},
        )
        == "observations_202802"
    )
    row = db.scalar(select(Observation))
    db.add(
        LatestObservation(
            source_id=source.id,
            metric="temperature",
            observation_id=row.id,
            observed_at=future + timedelta(hours=1),
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_comparable_daily_populations_keep_distinct_cutoffs(db, archive):
    from meteocentro.models import AggregateDirtyDay

    _, source, _, client = archive
    store(db, source, [sample(START + timedelta(hours=i), i) for i in range(23)])
    rebuild_day(db, source, DAY, now=START + timedelta(hours=12))
    db.commit()
    db.execute(delete(AggregateDirtyDay))
    db.commit()
    original = db.scalar(select(DailySummary))
    other_station = Station(
        name="Otro corte", province_code="28", moderation_status="active"
    )
    db.add(other_station)
    db.flush()
    other = StationSource(
        station_id=other_station.id,
        provider_id=source.provider_id,
        external_id="OTHER-CUTOFF",
        status="enabled",
    )
    db.add(other)
    db.flush()
    db.add(
        DailySummary(
            source_id=other.id,
            product=original.product,
            period_start=original.period_start,
            period_end=original.period_end - timedelta(hours=1),
            period_basis=original.period_basis,
            method=original.method,
            channel=original.channel,
            coverage=1,
            metrics=original.metrics,
            fetched_at=datetime.now(UTC),
            provisional=True,
        )
    )
    db.commit()
    result = client.get("/api/v1/daily", params={"day": str(DAY)}).json()
    assert len(result["extremes"]) == 4
    assert all(g["population"] == 1 for g in result["extremes"])


def test_populated_partition_migration_preserves_ids_revisions_and_latest(db, archive):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    _, source, _, _ = archive
    store(db, source, [sample(START, 0), sample(END + timedelta(days=40), 9)])
    rows = list(db.scalars(select(Observation).order_by(Observation.observed_at)))
    db.add(
        LatestObservation(
            source_id=source.id,
            metric="temperature",
            observation_id=rows[-1].id,
            observed_at=rows[-1].observed_at,
        )
    )
    db.add(
        ObservationRevision(
            observation_id=rows[0].id,
            observation_at=rows[0].observed_at,
            previous_metrics={"temperature": {"value": 1}},
            previous_quality={},
            previous_payload_hash="a" * 64,
        )
    )
    db.commit()
    expected = [(r.id, r.observed_at, r.metrics) for r in rows]
    db.close()
    config = Config(str(Path(__file__).resolve().parents[1] / "backend/alembic.ini"))
    command.downgrade(config, "0003_network_catalog")
    command.upgrade(config, "head")
    restored = list(db.scalars(select(Observation).order_by(Observation.observed_at)))
    assert [(r.id, r.observed_at, r.metrics) for r in restored] == expected
    assert db.scalar(select(ObservationRevision.observation_at)) == expected[0][1]
    assert db.scalar(select(LatestObservation.observation_id)) == expected[-1][0]
    assert db.scalar(
        text("SELECT tableoid::regclass::text FROM observations WHERE observed_at=:at"),
        {"at": START},
    ).startswith("observations_202603")

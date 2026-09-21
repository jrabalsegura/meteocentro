import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from meteocentro.aemet import (
    AemetAdapter,
    Batch,
    checked_url,
    digest,
    dms,
    normalize,
    retry_after,
    validate_metadata,
)
from meteocentro.config import Settings
from meteocentro.ingestion import Ingestor
from meteocentro.ingestion_errors import IngestionError, LeaseLost
from meteocentro.job_queue import Queue, db_now
from meteocentro.models import (
    Exclusion,
    IngestionRun,
    Job,
    LatestObservation,
    Observation,
    ObservationRevision,
    ProductMetadata,
    ProviderRuntime,
    Station,
    StationSource,
)
from meteocentro.worker import run_claim, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_phase1 import client_for

FIXTURES = Path(__file__).parent / "fixtures"
FIELDS = json.loads((FIXTURES / "aemet_metadata_sintetica.json").read_text())
ROW = json.loads((FIXTURES / "aemet_observacion_sintetica.json").read_text())[0]


def batch(rows=None, kind="current"):
    return Batch(
        rows if rows is not None else [dict(ROW)],
        FIELDS[kind],
        digest(FIELDS[kind]),
        datetime.now(UTC),
    )


@pytest.fixture
def queue(db, engine):
    settings = Settings(
        database_url=str(engine.url), aemet_api_key="synthetic-key", _env_file=None
    )
    value = Queue(engine, settings)
    value.schedule()
    return value


def due(queue, kind="current"):
    with Session(queue.engine) as db, db.begin():
        db.execute(
            update(Job)
            .where(Job.kind == kind)
            .values(status="pending", next_run_at=db_now(db) - timedelta(seconds=1))
        )
    claim = queue.claim(kind)
    assert claim
    return claim


def commit_batch(queue, data=None, kind="current"):
    claim = due(queue, kind)
    result, cursor = Ingestor(queue, claim).ingest(data or batch())
    queue.succeed(claim, result, cursor)
    return result


def transport_for(
    rows=None, *, error_status=None, envelope_status=200, pause=None, calls=None
):
    def handler(request):
        if calls is not None:
            calls.append(request)
        if request.url.path.endswith("/todas/") or request.url.path.endswith(
            "/todasestaciones/"
        ):
            if error_status:
                return httpx.Response(
                    error_status,
                    headers={"Retry-After": "90"},
                    text="secret raw response synthetic-key",
                )
            product = "current" if "/observacion/" in request.url.path else "inventory"
            return httpx.Response(
                200,
                json={
                    "estado": envelope_status,
                    "datos": f"https://opendata.aemet.es/opendata/sh/{product}/data",
                    "metadatos": f"https://opendata.aemet.es/opendata/sh/{product}/meta",
                },
            )
        product = "current" if "/current/" in request.url.path else "inventory"
        if request.url.path.endswith("/meta"):
            return httpx.Response(
                200,
                content=json.dumps(
                    {"campos": FIELDS[product]}, ensure_ascii=False
                ).encode("iso-8859-1"),
            )
        if pause:
            pause()
        return httpx.Response(200, json=rows if rows is not None else [ROW])

    return httpx.MockTransport(handler)


def adapter_factory(transport):
    return lambda key, **kwargs: AemetAdapter(key, transport=transport, **kwargs)


def count(db, table):
    return db.scalar(select(func.count()).select_from(table))


def test_normalization_utc_periods_null_zero_and_alternative_rain():
    record = {**ROW, "fint": "2026-10-25T02:00:00", "prec": 0, "pacutp": 12, "hr": None}
    data = Batch(
        [record],
        FIELDS["current"],
        digest(FIELDS["current"]),
        datetime(2026, 10, 26, tzinfo=UTC),
    )
    items = normalize(record, data)
    assert items[0].observed_at == datetime(2026, 10, 25, 2, tzinfo=UTC)
    assert items[0].metrics["temperature"].value == 0
    assert items[0].metrics["humidity"].value is None
    assert items[0].metrics["pressure_station"].kind == "station_pressure"
    assert items[0].metrics["pressure_sea_level"].kind == "sea_level_pressure"
    assert items[1].period_end - items[1].period_start == timedelta(minutes=10)
    assert items[2].period_end - items[2].period_start == timedelta(minutes=60)
    assert items[2].metrics["rain"].value == 0
    record["prec"] = None
    assert normalize(record, data)[2].metrics["rain"].value == 12
    record["pres"] = -999
    pressure = normalize(record, data)[0].metrics["pressure_station"]
    assert pressure.value is None and pressure.original_value == -999
    assert pressure.plausibility_flags == ["outside_physical_range"]
    record["prec"] = "Ip"
    with pytest.raises(ValueError):
        normalize(record, data)
    assert dms("402500N", latitude=True) > 40
    assert dms("0034200W", latitude=False) < -3
    with pytest.raises(ValueError):
        dms("406000N", latitude=True)


def test_metadata_changed_period_or_units_fails_closed():
    validate_metadata("current", FIELDS["current"])
    for identity, description in [
        ("vv", "media 60 minutos m/s"),
        ("prec", "60 minutos cm"),
        ("pres", "presión reducida al mar hPa"),
    ]:
        fields = copy.deepcopy(FIELDS["current"])
        next(f for f in fields if f["id"] == identity)["descripcion"] = description
        with pytest.raises(IngestionError, match="metadata_contract_changed"):
            validate_metadata("current", fields)


@pytest.mark.parametrize(
    "url",
    [
        "http://opendata.aemet.es/opendata/x",
        "https://opendata.aemet.es.evil/opendata/x",
        "https://evilaemet.es/opendata/x",
        "https://secret@opendata.aemet.es/opendata/x",
        "https://opendata.aemet.es:444/opendata/x",
        "https://127.0.0.1/opendata/x",
        None,
    ],
)
def test_temporary_url_allowlist(url):
    with pytest.raises(IngestionError, match="unsafe_data_url"):
        checked_url(url)


def test_http_two_stage_latin1_cache_quota_and_secret_boundaries(queue, db):
    calls = []
    factory = adapter_factory(transport_for(calls=calls))
    assert (
        run_claim(queue, due(queue), adapter_factory=factory)["status"] == "succeeded"
    )
    assert len(calls) == 3
    assert calls[0].headers["api_key"] == "synthetic-key"
    assert "api_key" not in calls[1].headers and "api_key" not in calls[2].headers
    assert all("synthetic-key" not in str(request.url) for request in calls)
    assert (
        run_claim(queue, due(queue), adapter_factory=factory)["result"]["inserted"] == 0
    )
    assert len(calls) == 5  # fresh envelope, same day's metadata from PostgreSQL
    assert count(db, ProductMetadata) == 1
    assert db.scalar(select(ProviderRuntime.day_calls)) == 5
    assert "synthetic-key" not in json.dumps(status(queue), default=str)


@pytest.mark.parametrize(
    "http_status,expected",
    [
        (401, "invalid_credentials"),
        (403, "invalid_credentials"),
        (429, "rate_limited"),
        (503, "provider_transient"),
    ],
)
def test_http_failure_states_and_durable_pause(queue, db, http_status, expected):
    claim = due(queue)
    report = run_claim(
        queue,
        claim,
        adapter_factory=adapter_factory(transport_for(error_status=http_status)),
    )
    assert report["code"] == expected
    state = db.scalar(select(ProviderRuntime))
    job = db.get(Job, claim.job_id)
    assert job.cursor is None
    assert "synthetic-key" not in json.dumps(status(queue), default=str)
    if http_status in (401, 403):
        assert state.pause_reason == expected and job.status == "paused"
        queue.schedule()
        assert queue.claim() is None
        queue.resume()
        assert queue.claim("current")
    elif http_status == 429:
        assert state.blocked_until >= datetime.now(UTC) + timedelta(seconds=80)
        assert Queue(queue.engine, queue.settings).claim() is None
    else:
        assert job.status == "retry" and job.next_run_at > datetime.now(UTC)


def test_missing_key_no_network_and_timeout_is_sanitized(queue, db):
    calls = []
    queue.settings.aemet_api_key = None
    report = run_claim(
        queue, due(queue), adapter_factory=adapter_factory(transport_for(calls=calls))
    )
    assert report["code"] == "pending_access" and calls == []
    assert db.scalar(select(ProviderRuntime.day_calls)) == 0
    queue.resume()
    queue.settings.aemet_api_key = Settings(
        database_url=str(queue.engine.url), aemet_api_key="synthetic-key"
    ).aemet_api_key

    def timeout(request):
        raise httpx.ReadTimeout("secret URL and synthetic-key", request=request)

    report = run_claim(
        queue,
        queue.claim("current"),
        adapter_factory=adapter_factory(httpx.MockTransport(timeout)),
    )
    assert report["code"] == "http_timeout"
    assert "synthetic-key" not in json.dumps(status(queue), default=str)


def test_response_size_redirect_and_envelope_error():
    for response, code in [
        (httpx.Response(200, content=b"x" * 21), "response_too_large"),
        (
            httpx.Response(302, headers={"Location": "https://evil.test"}),
            "unexpected_http",
        ),
    ]:
        adapter = AemetAdapter(
            "synthetic-key",
            max_bytes=20,
            transport=httpx.MockTransport(lambda _, response=response: response),
        )
        with pytest.raises(IngestionError, match=code):
            adapter.download("current")
        adapter.close()
    adapter = AemetAdapter(
        "synthetic-key", transport=transport_for(envelope_status=401)
    )
    with pytest.raises(IngestionError, match="invalid_credentials"):
        adapter.download("current")
    adapter.close()
    future = retry_after("Wed, 16 Sep 2037 12:00:00 GMT")
    assert future.year == 2037


def test_aemet_envelope_429_preserves_retry_after_on_http_200(queue, db):
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, json={"estado": 429}, headers={"Retry-After": "600"}
        )
    )
    result = run_claim(queue, due(queue), adapter_factory=adapter_factory(transport))
    assert result["code"] == "rate_limited"
    state = db.scalar(select(ProviderRuntime))
    assert state.blocked_until > datetime.now(UTC) + timedelta(seconds=590)


def test_idempotence_correction_and_monotonic_latest(queue, db):
    first = commit_batch(queue)
    second = commit_batch(queue)
    assert first["inserted"] == 3 and second["unchanged"] == 3
    assert count(db, Observation) == 3 and count(db, StationSource) == 1
    older = {**ROW, "fint": "2026-01-15T11:00:00+0000", "ta": 30}
    commit_batch(queue, batch([older]))
    latest = db.scalar(
        select(LatestObservation).where(LatestObservation.metric == "temperature")
    )
    assert latest.observed_at.astimezone(UTC).hour == 12
    commit_batch(queue, batch([{**ROW, "ta": 2}]))
    db.expire_all()
    assert count(db, ObservationRevision) == 3
    assert (
        db.get(Observation, (latest.observation_id, latest.observed_at)).metrics[
            "temperature"
        ]["value"]
        == "2"
    )
    commit_batch(queue, batch([{**ROW, "ta": None}]))
    db.expire_all()
    latest = db.scalar(
        select(LatestObservation).where(LatestObservation.metric == "temperature")
    )
    assert latest.observed_at.astimezone(UTC).hour == 11


def test_catalog_union_counts_and_exclusion_survives_discovery(queue, db):
    commit_batch(queue)
    source = db.scalar(select(StationSource))
    original_id = source.id
    inventory = [
        {
            "indicativo": identity,
            "nombre": "Histórica sintética",
            "latitud": "402500N",
            "longitud": "0034214W",
            "altitud": "667",
            "provincia": "OTRA",
        }
        for identity in ("T000X", "HIST0")
    ]
    result = commit_batch(queue, batch(inventory, "inventory"), "inventory")
    assert result["new_sources"] == 1 and result["valid"] == 2
    db.expire_all()
    assert db.get(StationSource, original_id).capabilities == {
        "current": True,
        "daily_history": True,
    }
    historical = db.scalar(
        select(StationSource).where(StationSource.external_id == "HIST0")
    )
    assert historical.capabilities == {"daily_history": True}
    db.add(Exclusion(source_id=source.id))
    db.commit()
    result = commit_batch(queue, batch(inventory, "inventory"), "inventory")
    assert result["excluded"] == 1 and count(db, StationSource) == 2
    outside = {**ROW, "idema": "OUT0", "lat": 41.39, "lon": 2.17}
    invalid = {**ROW, "idema": "BAD0", "lat": None}
    result = commit_batch(queue, batch([outside, invalid, ROW]))
    assert (result["outside"], result["invalid"], result["excluded"]) == (1, 1, 1)


def test_crash_after_commit_before_ack_recovers_with_fencing(queue, db):
    claim = due(queue)

    class Crash(BaseException):
        pass

    def crash():
        raise Crash()

    with pytest.raises(Crash):
        Ingestor(queue, claim).ingest(batch(), after_chunk=crash)
    assert count(db, Observation) == 3
    assert db.get(Job, claim.job_id).cursor is None
    with Session(queue.engine) as other, other.begin():
        other.execute(
            update(Job)
            .where(Job.id == claim.job_id)
            .values(lease_until=db_now(other) - timedelta(seconds=1))
        )
    replacement = Queue(queue.engine, queue.settings).claim("current")
    assert replacement.token != claim.token
    with pytest.raises(LeaseLost):
        queue.succeed(claim, {}, {"bad": True})
    with pytest.raises(LeaseLost):
        Ingestor(queue, claim).ingest(batch())
    result, cursor = Ingestor(queue, replacement).ingest(batch())
    queue.succeed(replacement, result, cursor)
    assert result["inserted"] == 0 and result["unchanged"] == 3
    db.expire_all()
    assert db.get(IngestionRun, claim.run_id).status == "abandoned"
    assert (
        db.get(Job, claim.job_id).cursor["sources"]["T000X"].startswith("2026-01-15T12")
    )


def test_quota_reservations_are_persistent_and_keep_current_reserve(queue, db):
    queue.settings.aemet_daily_http_budget = 5
    queue.settings.aemet_current_reserve = 3
    claim = due(queue, "inventory")
    queue.reserve_http(claim)
    queue.reserve_http(claim)
    with pytest.raises(IngestionError, match="daily_budget"):
        queue.reserve_http(claim)
    queue.succeed(claim, {}, {})
    claim = due(queue)
    Queue(queue.engine, queue.settings).reserve_http(claim)
    queue.reserve_http(claim)
    queue.reserve_http(claim)
    with pytest.raises(IngestionError, match="daily_budget"):
        queue.reserve_http(claim)
    assert db.scalar(select(ProviderRuntime.day_calls)) == 5


def test_minute_quota_and_concurrent_claims(queue, db):
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(lambda _: Queue(queue.engine, queue.settings).claim(), range(2))
        )
    claim = next(c for c in claims if c)
    assert sum(c is not None for c in claims) == 1 and claim.kind == "current"
    queue.settings.aemet_minute_http_budget = 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(queue.reserve_http, claim) for _ in range(2)]
        results = []
        for f in futures:
            try:
                f.result()
                results.append("reserved")
            except IngestionError as error:
                results.append(error.code)
    assert sorted(results) == ["minute_budget", "reserved"]
    assert db.scalar(select(ProviderRuntime.day_calls)) == 1
    before = db.get(Job, claim.job_id).lease_until
    queue.heartbeat(claim)
    db.expire_all()
    assert db.get(Job, claim.job_id).lease_until > before


def test_exclusion_during_http_download_prevents_storage_and_publication(queue, db):
    commit_batch(queue)
    source = db.scalar(select(StationSource))
    downloaded, release = threading.Event(), threading.Event()

    def paused():
        downloaded.set()
        assert release.wait(10)

    transport = transport_for(
        rows=[{**ROW, "fint": "2026-01-15T13:00:00+0000"}], pause=paused
    )
    claim = due(queue)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_claim, queue, claim, adapter_factory=adapter_factory(transport)
        )
        assert downloaded.wait(10)
        with Session(queue.engine) as other, other.begin():
            other.add(Exclusion(station_id=source.station_id))
        release.set()
        assert future.result()["result"]["excluded"] == 1
    assert count(db, Observation) == 3
    with client_for(db) as client:
        assert (
            client.get(f"/api/v1/stations/{source.station_id}/latest").status_code
            == 404
        )
        assert client.get("/api/v1/stations").json()["total"] == 0


def test_three_cycles_restart_gap_and_provider_outage_keep_archive(queue, db):
    for hour in (10, 11, 12):
        restarted = Queue(queue.engine, queue.settings)
        restarted.schedule()
        result = commit_batch(
            restarted, batch([{**ROW, "fint": f"2026-01-15T{hour}:00:00+0000"}])
        )
        assert result["inserted"] == 3
    assert count(db, Job) == 2 and count(db, IngestionRun) == 3
    next_batch = batch([{**ROW, "fint": "2026-01-17T12:00:00+0000"}])
    result = commit_batch(queue, next_batch)
    assert result["gaps"][0]["reason"] == "outside_available_window"
    source = db.scalar(select(StationSource))
    report = run_claim(
        queue,
        due(queue),
        adapter_factory=adapter_factory(transport_for(error_status=503)),
    )
    assert report["code"] == "provider_transient"
    with client_for(db) as client:
        response = client.get(f"/api/v1/stations/{source.station_id}/latest")
        assert response.status_code == 200 and len(response.json()["items"]) > 0


def test_database_failure_never_confirms_or_advances_cursor(queue, db, monkeypatch):
    claim = due(queue)
    original = queue.fence
    calls = 0

    def unavailable(session, value):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OperationalError("synthetic", {}, Exception("offline"))
        return original(session, value)

    monkeypatch.setattr(queue, "fence", unavailable)
    with pytest.raises(OperationalError):
        Ingestor(queue, claim).ingest(batch())
    assert count(db, Observation) == 0
    assert db.get(Job, claim.job_id).cursor is None
    assert db.get(IngestionRun, claim.run_id).status == "running"


@pytest.mark.parametrize("target", ["station", "source"])
def test_exclusion_committing_while_writer_waits_is_rechecked(queue, db, target):
    commit_batch(queue)
    source = db.scalar(select(StationSource))
    claim = due(queue)
    started = threading.Event()
    ingestor = Ingestor(queue, claim)
    original = ingestor.catalog
    writer_pid = []

    def catalog(session, *args):
        writer_pid.append(session.scalar(select(func.pg_backend_pid())))
        started.set()
        return original(session, *args)

    ingestor.catalog = catalog
    with (
        Session(queue.engine) as exclusion_db,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        exclusion_db.add(
            Exclusion(
                **{
                    target + "_id": source.station_id
                    if target == "station"
                    else source.id
                }
            )
        )
        exclusion_db.flush()  # Trigger takes the common station lock, not committed yet.
        future = pool.submit(
            ingestor.ingest, batch([{**ROW, "fint": "2026-01-15T13:00:00Z"}])
        )
        assert started.wait(5)
        # Observe real PostgreSQL lock contention before committing the exclusion.
        import time

        from sqlalchemy import text

        deadline = time.monotonic() + 5
        blocked = False
        while time.monotonic() < deadline:
            blocked = db.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks "
                    "WHERE pid=:pid AND NOT granted)"
                ),
                {"pid": writer_pid[0]},
            )
            if blocked:
                break
            time.sleep(0.01)
        try:
            assert blocked
        finally:
            exclusion_db.commit()
        result, cursor = future.result(timeout=5)
    queue.succeed(claim, result, cursor)
    assert result["excluded"] == 1 and count(db, Observation) == 3


def test_concurrent_catalog_creation_merges_capabilities_without_duplicate_identity(
    queue, db
):
    queue.settings.aemet_concurrency = 2
    current = queue.claim("current")
    inventory = queue.claim("inventory")
    record = {
        "indicativo": "T000X",
        "nombre": "Sintética",
        "latitud": "402500N",
        "longitud": "0034214W",
        "altitud": "667",
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [(current, batch()), (inventory, batch([record], "inventory"))]
        results = list(
            pool.map(lambda pair: Ingestor(queue, pair[0]).ingest(pair[1]), tasks)
        )
    for (claim, _), (result, cursor) in zip(tasks, results, strict=True):
        queue.succeed(claim, result, cursor)
    assert count(db, StationSource) == 1 and count(db, Station) == 1
    source = db.scalar(select(StationSource))
    assert source.capabilities == {"current": True, "daily_history": True}


def test_utc_daily_rollover_does_not_reset_minute_budget(queue, db):
    claim = due(queue)
    queue.settings.aemet_minute_http_budget = 1
    with Session(queue.engine) as other, other.begin():
        state = other.scalar(select(ProviderRuntime))
        state.day_start = db_now(other).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=1)
        state.day_calls = 400
        state.recent_calls = [db_now(other).isoformat()]
    with pytest.raises(IngestionError, match="minute_budget"):
        queue.reserve_http(claim)
    db.expire_all()
    state = db.scalar(select(ProviderRuntime))
    assert state.day_calls == 0 and state.day_start.astimezone(UTC).hour == 0


def test_historical_only_freshness_and_data_age_not_http_success(queue, db):
    record = {
        "indicativo": "HIST0",
        "nombre": "Sintética",
        "latitud": "402500N",
        "longitud": "0034214W",
        "altitud": "667",
    }
    commit_batch(queue, batch([record], "inventory"), "inventory")
    source = db.scalar(select(StationSource))
    with client_for(db) as client:
        assert (
            client.get(f"/api/v1/stations/{source.station_id}").json()["freshness"]
            == "historical_only"
        )
    commit_batch(queue)
    report = status(queue)
    assert report["data_state"] == "stale"
    assert report["runtime"]["last_polled_at"] >= datetime.now(UTC) - timedelta(
        seconds=5
    )
    assert report["runtime"]["newest_observed_at"].astimezone(UTC).month == 1


def test_partial_batch_commit_is_replayed_and_remaining_rows_recovered(queue, db):
    claim = due(queue)
    rows = [
        {
            **ROW,
            "fint": (
                datetime(2026, 1, 10, tzinfo=UTC) + timedelta(hours=hour)
            ).isoformat(),
        }
        for hour in range(26)
    ]

    class Crash(BaseException):
        pass

    def crash():
        raise Crash()

    with pytest.raises(Crash):
        Ingestor(queue, claim).ingest(batch(rows), after_chunk=crash)
    assert count(db, Observation) == 75
    with Session(queue.engine) as other, other.begin():
        other.execute(
            update(Job)
            .where(Job.id == claim.job_id)
            .values(lease_until=db_now(other) - timedelta(seconds=1))
        )
    recovered = queue.claim("current")
    result, cursor = Ingestor(queue, recovered).ingest(batch(rows))
    queue.succeed(recovered, result, cursor)
    assert result["unchanged"] == 75 and result["inserted"] == 3
    assert count(db, Observation) == 78


def test_bounded_retry_backoff_then_normal_cadence(queue, db):
    for expected_attempt, minimum_delay in [(1, 15), (2, 30), (3, 60), (4, 900)]:
        claim = due(queue)
        db.expire_all()
        assert db.get(Job, claim.job_id).attempts == expected_attempt
        # Scheduling uses PostgreSQL's clock, which can differ from the Mac host.
        before = db_now(db)
        queue.fail(claim, IngestionError("http_timeout"))
        after = db_now(db)
        db.expire_all()
        job = db.get(Job, claim.job_id)
        jitter = 5 if expected_attempt <= 3 else 0
        assert (
            before + timedelta(seconds=minimum_delay)
            <= job.next_run_at
            <= (after + timedelta(seconds=minimum_delay + jitter))
        )
    assert job.attempts == 0


def test_single_scheduling_leader_keeps_one_job_per_product(queue, db):
    from sqlalchemy import text

    queue.settings.aemet_poll_seconds = 1800
    with Session(queue.engine) as leader, leader.begin():
        leader.execute(text("SELECT pg_advisory_xact_lock(746302001)"))
        queue.schedule()
        assert (
            db.scalar(select(Job.interval_seconds).where(Job.kind == "current")) == 900
        )
    queue.schedule()
    assert db.scalar(select(Job.interval_seconds).where(Job.kind == "current")) == 1800
    assert count(db, Job) == 2


def test_changed_current_location_requires_review_without_rewriting_archive(queue, db):
    commit_batch(queue)
    source = db.scalar(select(StationSource))
    original_longitude = source.longitude
    result = commit_batch(
        queue, batch([{**ROW, "lon": -3.72, "fint": "2026-01-15T13:00:00Z"}])
    )
    assert result["location_review"] == 1 and result["invalid"] == 1
    db.expire_all()
    assert db.get(Station, source.station_id).moderation_status == "review"
    assert db.get(StationSource, source.id).longitude == original_longitude
    assert count(db, Observation) == 3
    with client_for(db) as client:
        assert client.get(f"/api/v1/stations/{source.station_id}").status_code == 404

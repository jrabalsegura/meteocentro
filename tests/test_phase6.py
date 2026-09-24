"""Real PostgreSQL, HTTP fixtures and independent worker/admin transactions."""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from meteocentro.administration import moderate
from meteocentro.aemet import AemetAdapter
from meteocentro.api import app
from meteocentro.auth import cookie_name, password_hash, password_matches
from meteocentro.config import Settings, get_settings
from meteocentro.history import rebuild_day
from meteocentro.history_import import enqueue_history
from meteocentro.ingestion import Ingestor
from meteocentro.ingestion_errors import LeaseLost
from meteocentro.job_queue import Queue
from meteocentro.models import (
    AdminSession,
    AdminUser,
    AuditEvent,
    CatalogVersion,
    DailySummary,
    Exclusion,
    IngestionRun,
    Job,
    Observation,
    Provider,
    ProviderRuntime,
    StationSource,
)
from meteocentro.worker import run_claim
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session
from test_phase1 import client_for
from test_phase2 import (
    ROW,
    adapter_factory,
    batch,
    commit_batch,
    due,
    transport_for,
)
from test_phase2 import (
    queue as queue,  # noqa: PLC0414 -- pytest fixture re-export
)
from test_phase5 import (
    DAILY,
    DAY,
    START,
    daily_transport,
    params,
    sample,
    store,
)
from test_phase5 import (
    archive as archive,  # noqa: PLC0414 -- pytest fixture re-export
)

PASSWORD = "Synthetic test password, never a real credential"
ORIGIN = "http://localhost:5173"


@pytest.fixture(scope="module")
def encoded_password():
    return password_hash(PASSWORD)


@pytest.fixture
def admin(db, monkeypatch, encoded_password):
    monkeypatch.setattr(get_settings(), "app_origin", ORIGIN)
    user = AdminUser(username="owner", password_hash=encoded_password)
    db.add(user)
    db.commit()
    client = client_for(db)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200, response.text
    client.headers.update(
        {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    )
    return client, user


def post(client, target, action="exclude", body=None):
    response = client.post(f"/api/v1/admin/{target}/{action}", json=body or {})
    assert response.status_code == 200, response.text
    return response


def public_paths(station, source):
    root = f"/api/v1/stations/{station.id}"
    return [
        (root, {}),
        (root + "/current", {}),
        (root + "/latest", {}),
        (
            root + "/observations",
            {
                "start": START.isoformat(),
                "end": (START + timedelta(days=1)).isoformat(),
            },
        ),
        (
            root + "/daily-summaries",
            {
                "start": START.isoformat(),
                "end": (START + timedelta(days=1)).isoformat(),
            },
        ),
        *[
            (root + "/" + suffix, params(source))
            for suffix in ["series", "daily", "records", "export.csv"]
        ],
    ]


def test_password_hash_and_default_private_policy(encoded_password, monkeypatch):
    monkeypatch.delenv("PRIVATE_READ", raising=False)
    assert encoded_password.startswith("scrypt$131072$8$1$")
    assert PASSWORD not in encoded_password
    assert password_matches(PASSWORD, encoded_password)
    assert not password_matches("incorrect", encoded_password)
    assert password_hash(PASSWORD) != encoded_password
    assert Settings(
        database_url="postgresql+psycopg://test@localhost/test",
        _env_file=None,
    ).private_read
    with pytest.raises(ValueError):
        Settings(
            database_url="postgresql+psycopg://test@localhost/test",
            environment="production",
            _env_file=None,
        )


def route_path(path):
    import re

    return re.sub(
        r"\{([^}]+)\}",
        lambda m: (
            "aemet"
            if m[1] == "provider_code"
            else "exclude"
            if m[1] == "action"
            else str(uuid.uuid4())
        ),
        path,
    )


def test_every_private_read_and_admin_mutation_requires_backend_session(
    db, monkeypatch
):
    monkeypatch.setattr(get_settings(), "private_read", True)
    with client_for(db) as visitor:
        checked = 0
        for path, methods in app.openapi()["paths"].items():
            if not path.startswith("/api/v1/") or path in {
                "/api/v1/auth/login",
                "/api/v1/auth/session",
            }:
                continue
            method = "POST" if "post" in methods else "GET"
            response = visitor.request(method, route_path(path))
            assert response.status_code == 401, (path, response.text)
            assert response.headers["cache-control"] == "no-store"
            checked += 1
        for path in ["/api/v1/openapi.json", "/api/v1/docs"]:
            assert visitor.get(path).status_code == 401
        assert checked >= 30
    monkeypatch.setattr(get_settings(), "private_read", False)
    with client_for(db) as visitor:
        assert visitor.get("/api/v1/stations").status_code == 200
        assert visitor.get("/api/v1/admin/stations").status_code == 401
        assert (
            visitor.post(
                f"/api/v1/admin/stations/{uuid.uuid4()}/exclude", json={}
            ).status_code
            == 401
        )


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": ORIGIN},
        {"Origin": "https://hostile.invalid", "X-CSRF-Token": "bad"},
        {"X-CSRF-Token": "bad"},
        {"Origin": ORIGIN, "X-CSRF-Token": "bad"},
    ],
)
def test_csrf_and_origin_block_admin_mutations(db, admin, headers):
    client, _ = admin
    client.headers.clear()
    response = client.post(
        f"/api/v1/admin/stations/{uuid.uuid4()}/exclude", json={}, headers=headers
    )
    assert response.status_code == 403
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_one_year_session_cookie_and_absolute_expiry(db, admin, monkeypatch):
    from meteocentro import auth

    monkeypatch.delenv("SESSION_HOURS", raising=False)
    settings = Settings(
        database_url="postgresql+psycopg://test@localhost/test",
        _env_file=None,
    )
    assert settings.session_hours == 365 * 24
    with pytest.raises(ValueError):
        Settings(database_url=settings.database_url, session_hours=8761, _env_file=None)
    monkeypatch.setattr(get_settings(), "session_hours", settings.session_hours)
    client, _ = admin
    before = datetime.now(UTC)
    response = client.post(
        "/api/v1/auth/login", json={"username": "owner", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert "Max-Age=31536000" in response.headers["set-cookie"]
    expiry = datetime.fromisoformat(response.json()["expires_at"])
    assert (
        before + timedelta(days=365)
        <= expiry
        <= datetime.now(UTC) + timedelta(days=365)
    )
    stored = db.scalar(select(AdminSession).where(AdminSession.revoked_at.is_(None)))
    assert stored.expires_at == expiry
    # Reading the session does not silently renew its absolute lifetime.
    monkeypatch.setattr(auth, "db_now", lambda _db: expiry - timedelta(seconds=1))
    info = client.get("/api/v1/auth/session").json()
    assert info["authenticated"] is True
    assert datetime.fromisoformat(info["expires_at"]) == expiry
    monkeypatch.setattr(auth, "db_now", lambda _db: expiry)
    assert client.get("/api/v1/auth/session").json()["authenticated"] is False


def test_cookies_rotation_expiry_revocation_and_disabled_user(db, admin, monkeypatch):
    client, user = admin
    old_token = client.cookies.get(cookie_name())
    login = client.post(
        "/api/v1/auth/login", json={"username": "owner", "password": PASSWORD}
    )
    assert login.status_code == 200
    cookie = login.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
    assert client.cookies.get(cookie_name()) != old_token
    with client_for(db) as stale:
        stale.cookies.set(cookie_name(), old_token)
        assert stale.get("/api/v1/admin/stations").status_code == 401
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    assert client.get("/api/v1/admin/stations").status_code == 200
    user.enabled = False
    db.commit()
    assert client.get("/api/v1/admin/stations").status_code == 401
    user.enabled = True
    db.execute(
        update(AdminSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    db.commit()
    assert client.post("/api/v1/auth/logout", json={}).status_code == 401
    monkeypatch.setattr(get_settings(), "environment", "production")
    monkeypatch.setattr(get_settings(), "app_origin", "https://weather.test")
    with TestClient(app, base_url="https://weather.test") as secure:
        response = secure.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://weather.test"},
            json={"username": "owner", "password": PASSWORD},
        )
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
        assert response.headers["set-cookie"].startswith("__Host-meteocentro=")
        secure.headers.update(
            {
                "Origin": "https://weather.test",
                "X-CSRF-Token": response.json()["csrf_token"],
            }
        )
        assert secure.post("/api/v1/auth/revoke-all", json={}).status_code == 200
        assert secure.get("/api/v1/admin/stations").status_code == 401


def test_login_limits_persist_across_clients_and_validation_never_echoes_password(
    db, admin
):
    client, _ = admin
    for _ in range(4):
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": "owner", "password": "wrong"}
            ).status_code
            == 401
        )
    with client_for(db) as restarted:
        response = restarted.post(
            "/api/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "owner", "password": PASSWORD},
        )
        assert response.status_code == 429
        assert response.headers["retry-after"] == "900"
        leaked = "sensitive" * 200
        response = restarted.post(
            "/api/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "owner", "password": leaked},
        )
        assert response.status_code == 422 and leaked not in response.text
        assert "sensitive" not in response.text
        assert (
            restarted.post(
                "/api/v1/auth/login",
                headers={"Origin": "https://hostile.invalid"},
                json={"username": "owner", "password": PASSWORD},
            ).status_code
            == 403
        )


@pytest.mark.parametrize("target", ["stations", "sources"])
def test_exclude_restore_all_read_routes_csv_and_archive_with_audit(
    db, archive, admin, target
):
    station, source, _, _ = archive
    client, user = admin
    store(db, source, [sample(START, 0)])
    rebuild_day(db, source, DAY)
    db.commit()
    requests = public_paths(station, source)
    for path, query in requests:
        response = client.get(path, params=query)
        assert response.status_code == 200, (path, response.text)
        assert response.headers["cache-control"] == "no-store"
    count = db.scalar(select(func.count()).select_from(Observation))
    version = client.get("/api/v1/map").headers["x-catalog-version"]
    target_path = f"{target}/{station.id if target == 'stations' else source.id}"
    post(client, target_path, body={"reason": "Sensor defectuoso"})
    for path, query in requests:
        # Conditional requests cannot retrieve a previously generated export or projection.
        response = client.get(
            path,
            params=query,
            headers={
                "If-None-Match": version,
                "If-Modified-Since": "Mon, 21 Sep 2026 00:00:00 GMT",
            },
        )
        assert response.status_code == 404, (path, response.text)
    assert client.get("/api/v1/stations").json()["total"] == 0
    for path in ["/api/v1/map", "/api/v1/map?q=Sintética", f"/api/v1/daily?day={DAY}"]:
        response = client.get(path)
        assert str(station.id) not in response.text
        assert response.headers["x-catalog-version"] != version
    assert client.get(f"/api/v1/admin/stations/{station.id}/archive").json()["items"]
    assert client.get("/api/v1/admin/stations?state=excluded").json()["total"] == 1
    assert db.scalar(select(func.count()).select_from(Observation)) == count
    post(client, target_path)  # Idempotent exclusion, one active tombstone.
    assert db.scalar(select(func.count()).select_from(Exclusion)) == 1
    post(client, target_path, "restore")
    assert client.get(requests[-1][0], params=requests[-1][1]).status_code == 200
    events = db.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at)).all()
    assert len(events) == 2 and all(e.actor_id == user.id for e in events)
    assert events[0].details["archive_preserved"]
    assert db.scalar(select(Exclusion)).revoked_at is not None


def test_transaction_rollback_keeps_exclusion_jobs_version_and_audit_atomic(
    db, archive, admin
):
    station, source, _, _ = archive
    _, user = admin
    enqueue_history(db, source.id, DAY, DAY + timedelta(days=1))
    db.commit()
    moderate(db, user.id, station_id=station.id)
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Exclusion)) == 0
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    assert db.scalar(select(Job.status).where(Job.kind == "history")) == "pending"
    assert db.scalar(select(CatalogVersion.version)) in {None, 0}


def test_linked_sources_source_pause_inheritance_and_explicit_restore(
    db, archive, admin
):
    station, source, _, _ = archive
    client, _ = admin
    second_provider = Provider(
        code="meteoclimatic", name="Meteoclimatic", status="verified"
    )
    db.add(second_provider)
    db.flush()
    sibling = StationSource(
        station_id=station.id,
        provider_id=second_provider.id,
        external_id="ESMAD00001",
        latitude=40.4,
        longitude=-3.7,
        status="enabled",
    )
    db.add(sibling)
    db.commit()
    post(client, f"sources/{source.id}")
    assert client.get(f"/api/v1/stations/{station.id}").json()["sources"][0][
        "id"
    ] == str(sibling.id)
    post(client, f"stations/{station.id}")
    assert client.get(f"/api/v1/stations/{station.id}").status_code == 404
    # Splitting cannot evade an inherited exclusion.
    split = post(
        client,
        f"sources/{sibling.id}/review",
        "split",
        {"evidence": "Two different instruments"},
    ).json()
    new_station = split["station_id"]
    assert client.get(f"/api/v1/stations/{new_station}").status_code == 404
    post(client, f"stations/{station.id}", "restore")
    assert (
        client.get(f"/api/v1/stations/{station.id}").status_code == 404
    )  # own exclusion survives
    post(client, f"sources/{source.id}", "restore")
    assert client.get(f"/api/v1/stations/{station.id}").status_code == 200
    post(client, f"sources/{sibling.id}", "restore")
    assert client.get(f"/api/v1/stations/{new_station}").status_code == 200
    post(client, f"stations/{station.id}")
    post(
        client,
        f"sources/{sibling.id}/review",
        "link",
        {"station_id": str(station.id), "evidence": "Verified common site"},
    )
    assert client.get(f"/api/v1/stations/{station.id}").status_code == 404
    assert all(
        s["excluded"]
        for s in client.get(f"/api/v1/admin/stations/{station.id}").json()["sources"]
    )


def test_exclusion_survives_rediscovery_and_new_queue_and_api_sessions(
    db, queue, admin
):
    commit_batch(queue)
    source = db.scalar(select(StationSource))
    client, _ = admin
    post(client, f"stations/{source.station_id}")
    db.close()
    restarted = Queue(queue.engine, queue.settings)
    restarted.schedule()
    assert commit_batch(restarted)["excluded"] == 1
    with Session(queue.engine) as fresh:
        assert fresh.scalar(select(func.count()).select_from(StationSource)) == 1
        assert fresh.scalar(select(func.count()).select_from(Observation)) == 3
        with client_for(fresh) as visitor:
            assert visitor.get("/api/v1/stations").json()["total"] == 0


@pytest.mark.parametrize("target", ["stations", "sources"])
def test_api_exclusion_during_batch_download_blocks_inflight_worker(
    db, queue, admin, target
):
    commit_batch(queue)
    source = db.scalar(select(StationSource))
    client, _ = admin
    started, release = threading.Event(), threading.Event()

    def pause():
        started.set()
        assert release.wait(10)

    transport = transport_for(
        rows=[{**ROW, "fint": "2026-01-15T13:00:00Z"}], pause=pause
    )
    claim = due(queue)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_claim, queue, claim, adapter_factory=adapter_factory(transport)
        )
        assert started.wait(5)
        try:
            post(
                client,
                f"{target}/{source.station_id if target == 'stations' else source.id}",
            )
        finally:
            release.set()
        assert future.result(timeout=10)["result"]["excluded"] == 1
    assert db.scalar(select(func.count()).select_from(Observation)) == 3


def test_api_exclusion_cancels_running_history_and_old_claim_cannot_commit_after_restore(
    db, archive, admin
):
    station, source, queue, _ = archive
    client, _ = admin
    enqueue_history(db, source.id, DAY, DAY + timedelta(days=1))
    db.commit()
    claim = queue.claim("history")
    started, release = threading.Event(), threading.Event()

    def pause():
        started.set()
        assert release.wait(10)

    factory = lambda key, **kw: AemetAdapter(
        key, transport=daily_transport([DAILY], hook=pause), **kw
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_claim, queue, claim, adapter_factory=factory)
        assert started.wait(5)
        try:
            post(client, f"stations/{station.id}")
            job = db.get(Job, claim.job_id)
            assert job.status == "cancelled" and job.owner_token is None
            assert db.get(IngestionRun, claim.run_id).status == "cancelled"
            post(client, f"stations/{station.id}", "restore")
        finally:
            release.set()
        with pytest.raises(LeaseLost):
            future.result(timeout=10)
    assert db.scalar(select(func.count()).select_from(DailySummary)) == 0
    db.expire_all()
    assert db.get(Job, claim.job_id).status == "pending"
    resumed = queue.claim("history")
    assert resumed.token != claim.token
    assert (
        run_claim(
            queue,
            resumed,
            adapter_factory=lambda key, **kw: AemetAdapter(
                key, transport=daily_transport([DAILY]), **kw
            ),
        )["result"]["inserted"]
        == 6
    )


def test_worker_waiting_on_exclusion_transaction_cannot_republish(db, queue, admin):
    commit_batch(queue)
    source = db.scalar(select(StationSource))
    _, user = admin
    claim = due(queue)
    writer_pid = []
    started = threading.Event()
    ingestor = Ingestor(queue, claim)
    original = ingestor.catalog

    def observed(session, *args):
        writer_pid.append(session.scalar(select(func.pg_backend_pid())))
        started.set()
        return original(session, *args)

    ingestor.catalog = observed
    with (
        Session(queue.engine) as transaction,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        moderate(transaction, user.id, station_id=source.station_id)
        future = pool.submit(
            ingestor.ingest, batch([{**ROW, "fint": "2026-01-15T13:00:00Z"}])
        )
        assert started.wait(5)
        deadline = time.monotonic() + 5
        blocked = False
        while time.monotonic() < deadline:
            blocked = db.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)"
                ),
                {"pid": writer_pid[0]},
            )
            if blocked:
                break
            time.sleep(0.01)
        try:
            assert blocked
        finally:
            transaction.commit()
        result, cursor = future.result(timeout=5)
    queue.succeed(claim, result, cursor)
    assert result["excluded"] == 1
    assert db.scalar(select(func.count()).select_from(Observation)) == 3


def test_discovery_deduplicates_limits_inputs_and_verifies_on_worker(db, queue, admin):
    client, _ = admin
    invalid = client.post(
        "/api/v1/admin/discovery/aemet",
        json={"external_id": "https://internal.invalid"},
    )
    assert invalid.status_code in {409, 422}
    assert (
        client.post("/api/v1/admin/discovery/wunderground", json={}).status_code == 422
    )
    response = client.post(
        "/api/v1/admin/discovery/aemet", json={"external_id": ROW["idema"]}
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["job"]["id"]
    repeated = client.post(
        "/api/v1/admin/discovery/aemet", json={"external_id": ROW["idema"]}
    )
    assert repeated.json()["already_active"] and repeated.json()["job"]["id"] == job_id
    assert (
        client.post(
            "/api/v1/admin/discovery/aemet", json={"external_id": "OTHER1"}
        ).status_code
        == 409
    )

    class Adapter:
        def __init__(self, *args, **kwargs):
            pass

        def download(self, product):
            return batch() if product == "current" else batch([], "inventory")

        def close(self):
            pass

    claim = queue.claim("verify")
    result = run_claim(queue, claim, adapter_factory=Adapter)["result"]
    assert result["found"] and result["new_sources"] == 1
    db.expire_all()
    assert db.get(Job, uuid.UUID(job_id)).status == "completed"
    assert (
        db.scalar(select(func.count()).select_from(Observation)) == 0
    )  # only catalog verification


def test_private_responses_never_expose_provider_credentials_or_raw_errors(
    db, queue, admin
):
    client, _ = admin
    provider = db.scalar(select(Provider))
    provider.capabilities = {
        "api_key": "sensitive-key",
        "discover": True,
        "profile_robots": {"text": "secret"},
    }
    runtime = db.get(ProviderRuntime, provider.id)
    runtime.pause_reason = "https://signed.invalid/?key=sensitive-key"
    job = db.scalar(select(Job).limit(1))
    db.add(
        IngestionRun(
            job_id=job.id,
            started_at=datetime.now(UTC),
            status="failed",
            error_code="Authorization: sensitive-key",
            result={"payload": "sensitive-key", "new_sources": 1},
        )
    )
    db.commit()
    for path in ["/api/v1/admin/providers", "/api/v1/admin/jobs", "/api/v1/providers"]:
        response = client.get(path)
        assert response.status_code == 200
        assert (
            "sensitive-key" not in response.text
            and "signed.invalid" not in response.text
        )


def test_review_coordinates_and_duplicate_decisions_keep_exclusions(db, archive, admin):
    from collections import Counter

    from meteocentro.catalog import suggest_duplicates
    from meteocentro.models import DuplicateCandidate, Station

    station, source, _, _ = archive
    client, user = admin
    source.review_reason = "missing_coordinates"
    station.moderation_status = "review"
    db.commit()
    post(client, f"stations/{station.id}")
    response = client.post(
        f"/api/v1/admin/sources/{source.id}/review/location",
        json={
            "latitude": 40.4,
            "longitude": -3.7,
            "precision": "minute",
            "evidence": "Posición contrastada",
        },
    )
    assert response.status_code == 200
    assert client.get(f"/api/v1/stations/{station.id}").status_code == 404
    post(client, f"stations/{station.id}", "restore")
    assert client.get(f"/api/v1/stations/{station.id}").status_code == 200
    other_provider = Provider(
        code="meteoclimatic", name="Meteoclimatic", status="verified"
    )
    other = Station(
        name="Otra estación",
        latitude=40.4,
        longitude=-3.7,
        province_code="28",
        moderation_status="review",
    )
    db.add_all([other_provider, other])
    db.flush()
    second = StationSource(
        station_id=other.id,
        provider_id=other_provider.id,
        external_id="ESMAD00002",
        latitude=40.4,
        longitude=-3.7,
        status="enabled",
        review_reason="potential_duplicate",
    )
    db.add(second)
    db.flush()
    pair = sorted([source.id, second.id])
    candidate = DuplicateCandidate(
        source_id=pair[0], other_source_id=pair[1], distance_m=0, reason="proximity"
    )
    db.add(candidate)
    db.commit()
    post(
        client,
        f"duplicates/{candidate.id}",
        "distinct",
        {"evidence": "Dos instrumentos distintos"},
    )
    assert db.get(Station, other.id).moderation_status == "active"
    db.refresh(second)  # PostgreSQL Numeric values, as in the worker's fresh session.
    suggest_duplicates(
        db, second, {"name": other.name, "precision": "minute"}, Counter()
    )
    db.commit()
    assert second.review_reason is None  # Rediscovery does not undo an explicit review.
    assert all(e.actor_id == user.id for e in db.scalars(select(AuditEvent)))


def test_manual_verification_cannot_recreate_excluded_identity(db, queue, admin):
    from meteocentro.models import IdentityExclusion

    client, _ = admin
    provider = db.scalar(select(Provider))
    db.add(IdentityExclusion(provider_id=provider.id, external_id=ROW["idema"]))
    db.commit()
    response = client.post(
        "/api/v1/admin/discovery/aemet", json={"external_id": ROW["idema"]}
    )
    assert response.status_code == 200

    class Adapter:
        def __init__(self, *args, **kwargs):
            pass

        def download(self, product):
            return batch() if product == "current" else batch([], "inventory")

        def close(self):
            pass

    result = run_claim(queue, queue.claim("verify"), adapter_factory=Adapter)["result"]
    assert result["excluded"] == 1
    assert db.scalar(select(func.count()).select_from(StationSource)) == 0


def test_retry_never_bypasses_cooldown_or_restarts_cancelled_job(db, queue, admin):
    client, _ = admin
    job = db.scalar(select(Job).where(Job.kind == "inventory"))
    job.status, job.next_run_at = "retry", datetime.now(UTC) + timedelta(hours=1)
    db.commit()
    path = f"/api/v1/admin/jobs/{job.id}/retry"
    assert client.post(path, json={}).status_code == 409
    job.next_run_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert client.post(path, json={}).status_code == 200
    job.status = "cancelled"
    db.commit()
    assert client.post(path, json={}).status_code == 409


def test_exclusion_waits_for_writer_then_cancels_without_deadlock(db, archive, admin):
    station, source, queue, _ = archive
    _, user = admin
    enqueue_history(db, source.id, DAY, DAY + timedelta(days=1))
    db.commit()
    claim = queue.claim("history")
    started = threading.Event()
    pids = []

    def exclude():
        with Session(queue.engine) as other, other.begin():
            pids.append(other.scalar(select(func.pg_backend_pid())))
            started.set()
            return moderate(other, user.id, station_id=station.id)

    with (
        Session(queue.engine) as writer,
        writer.begin(),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        queue.fence(writer, claim)
        writer.execute(
            select(StationSource.station_id).where(StationSource.id == source.id)
        )
        future = pool.submit(exclude)
        assert started.wait(5)
        deadline = time.monotonic() + 5
        blocked = False
        while time.monotonic() < deadline:
            blocked = db.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)"
                ),
                {"pid": pids[0]},
            )
            if blocked:
                break
            time.sleep(0.01)
        try:
            assert blocked
        finally:
            writer.commit()
        assert future.result(timeout=5)["jobs_changed"] == 1
    with pytest.raises(LeaseLost):
        queue.heartbeat(claim)

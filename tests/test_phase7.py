"""Operational failures with real PostgreSQL; no external requests."""

from datetime import UTC, datetime, timedelta

from meteocentro.models import Provider, ProviderRuntime, WorkerHeartbeat
from meteocentro.operations import snapshot, touch_worker
from sqlalchemy import update

# Explicit fixture re-exports let pytest discover dependencies and avoid F811.
from test_phase6 import admin as admin  # noqa: PLC0414
from test_phase6 import encoded_password as encoded_password  # noqa: PLC0414


def test_missing_worker_then_real_heartbeat(db, engine):
    assert snapshot(db)["worker"]["state"] == "missing"
    touch_worker(engine)
    db.rollback()  # fresh READ COMMITTED transaction after the separate connection
    assert snapshot(db)["worker"]["state"] == "alive"
    db.execute(
        update(WorkerHeartbeat).values(
            seen_at=datetime.now(UTC) - timedelta(seconds=100)
        )
    )
    db.commit()
    assert snapshot(db)["status"] == "degraded"
    assert snapshot(db)["worker"]["state"] == "missing"


def test_provider_responds_without_recent_observations(db):
    now = datetime.now(UTC)
    p = Provider(
        code="aemet",
        name="SYNTHETIC",
        status="verified",
        capabilities={"stale_after_seconds": 5400},
        poll_interval_seconds=900,
    )
    db.add(p)
    db.flush()
    r = ProviderRuntime(
        provider_id=p.id,
        day_start=now,
        day_calls=0,
        recent_calls=[],
        last_polled_at=now,
        newest_observed_at=now - timedelta(hours=3),
        last_new_data_at=now - timedelta(hours=3),
    )
    db.add(r)
    db.commit()
    assert snapshot(db)["providers"][0]["state"] == "responding_without_fresh_data"
    r.newest_observed_at = now
    db.commit()
    assert snapshot(db)["providers"][0]["state"] == "fresh"
    r.last_polled_at = now - timedelta(hours=1)
    db.commit()
    assert snapshot(db)["providers"][0]["state"] == "poll_overdue"
    p.status = "disabled"
    db.commit()
    assert snapshot(db)["providers"][0]["state"] == "disabled"


def test_private_operational_diagnostics(db, admin):
    client, _ = admin
    result = client.get("/api/v1/admin/operations")
    assert result.status_code == 200
    assert result.json()["worker"]["state"] == "missing"
    assert "no-store" in result.headers["Cache-Control"]
    assert (
        client.get("/api/v1/admin/providers").json()["operations"]["overdue_jobs"] == 0
    )
    client.cookies.clear()
    assert client.get("/api/v1/admin/operations").status_code == 401


def test_migration_rejects_a_concurrent_deployer(engine, monkeypatch):
    import pytest
    from meteocentro.start import main
    from sqlalchemy import text

    monkeypatch.setattr("sys.argv", ["start", "migrate"])
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(746307001)"))
        try:
            with pytest.raises(SystemExit, match="migration_already_running"):
                main()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(746307001)"))

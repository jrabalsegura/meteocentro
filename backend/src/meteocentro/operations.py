"""Read-only diagnosis and independent worker heartbeat; no provider HTTP."""

import json
from datetime import timedelta
from threading import Event, Thread

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from meteocentro.db import get_engine
from meteocentro.models import Job, Provider, ProviderRuntime, WorkerHeartbeat


def touch_worker(engine):
    with engine.begin() as db:
        db.execute(
            insert(WorkerHeartbeat)
            .values(name="ingestion", seen_at=func.now())
            .on_conflict_do_update(index_elements=["name"], set_={"seen_at": func.now()})
        )


def start_heartbeat(engine, stopped: Event):
    def run():
        while not stopped.is_set():
            try:
                touch_worker(engine)
            except SQLAlchemyError:
                pass  # Database outage is reported by the main worker loop.
            stopped.wait(20)

    thread = Thread(target=run, name="worker-heartbeat", daemon=True)
    thread.start()
    return thread


def provider_state(provider, runtime, now):
    if provider.status != "verified":
        return provider.status
    if not runtime or not runtime.last_polled_at:
        return "no_polls"
    if (now - runtime.last_polled_at).total_seconds() > max(
        1800, (provider.poll_interval_seconds or 900) * 2
    ):
        return "poll_overdue"
    if not runtime.newest_observed_at:
        return "no_data"
    threshold = provider.capabilities.get("stale_after_seconds", 5400)
    if (now - runtime.newest_observed_at).total_seconds() > threshold:
        return "responding_without_fresh_data"
    return "fresh"


def snapshot(db):
    now = db.scalar(select(func.now()))
    seen = db.scalar(select(WorkerHeartbeat.seen_at).where(WorkerHeartbeat.name == "ingestion"))
    age = max(0, (now - seen).total_seconds()) if seen else None
    providers = [
        {
            "provider": provider.code,
            "state": provider_state(provider, runtime, now),
            "last_polled_at": runtime.last_polled_at if runtime else None,
            "last_new_data_at": runtime.last_new_data_at if runtime else None,
            "newest_observed_at": runtime.newest_observed_at if runtime else None,
        }
        for provider, runtime in db.execute(
            select(Provider, ProviderRuntime)
            .outerjoin(ProviderRuntime)
            .where(Provider.code.in_(["aemet", "meteoclimatic"]))
        )
    ]
    overdue = db.scalar(
        select(func.count())
        .select_from(Job)
        .join(Provider)
        .where(
            Provider.status == "verified",
            (
                (Job.status.in_(["pending", "retry"]))
                & (Job.next_run_at < now - timedelta(minutes=30))
            )
            | ((Job.status == "running") & (Job.lease_until < now)),
        )
    )
    worker_state = "alive" if age is not None and age < 90 else "missing"
    degraded = (
        worker_state != "alive"
        or overdue > 0
        or any(p["state"] not in {"fresh", "disabled"} for p in providers)
    )
    return {
        "status": "degraded" if degraded else "ok",
        "worker": {"state": worker_state, "last_seen_at": seen, "age_seconds": age},
        "overdue_jobs": overdue,
        "next_job_at": db.scalar(
            select(func.min(Job.next_run_at))
            .join(Provider)
            .where(Provider.status == "verified", Job.status.in_(["pending", "retry"]))
        ),
        "database_bytes": db.scalar(text("SELECT pg_database_size(current_database())")),
        "providers": providers,
    }


def main():
    try:
        with Session(get_engine()) as db:
            result = snapshot(db)
    except (SQLAlchemyError, ValueError):
        print(json.dumps({"status": "unavailable"}))
        return 2
    print(json.dumps(result, default=str))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Run with python -m meteocentro.worker; the API never imports this loop."""

import argparse
import json
import logging
import signal
from threading import Event, Thread

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from meteocentro.aemet import AemetAdapter
from meteocentro.config import get_settings
from meteocentro.db import get_engine
from meteocentro.ingestion import Ingestor
from meteocentro.ingestion_errors import IngestionError, LeaseLost
from meteocentro.job_queue import Queue, db_now
from meteocentro.meteoclimatic import MeteoclimaticAdapter, MeteoclimaticIngestor
from meteocentro.meteoclimatic_catalog import MeteoclimaticCatalog
from meteocentro.models import IngestionRun, Job, Provider, ProviderRuntime


def emit(**values):
    print(json.dumps(values, ensure_ascii=False, default=str), flush=True)


class Heartbeat:
    def __init__(self, queue, claim):
        self.queue, self.claim = queue, claim
        self.stop = Event()
        self.thread = Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.wait(self.queue.settings.worker_lease_seconds / 3):
            try:
                self.queue.heartbeat(self.claim)
            except (SQLAlchemyError, LeaseLost):
                return

    def __enter__(self):
        self.thread.start()

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=5)


def run_claim(queue, claim, *, adapter_factory=None, after_chunk=None):
    if queue.provider_code == "meteoclimatic":
        ingestor = MeteoclimaticIngestor(queue, claim)
        adapter = (adapter_factory or MeteoclimaticAdapter)(
            terms_reference=queue.settings.meteoclimatic_terms_reference,
            reserve=lambda: queue.reserve_http(claim),
        )
    else:
        ingestor = Ingestor(queue, claim)
        secret = queue.settings.aemet_api_key
        adapter = (adapter_factory or AemetAdapter)(
            secret.get_secret_value() if secret else None,
            reserve=lambda: queue.reserve_http(claim),
            get_metadata=ingestor.get_metadata,
            save_metadata=ingestor.save_metadata,
        )
    try:
        with Heartbeat(queue, claim):
            if queue.provider_code == "meteoclimatic" and claim.kind == "catalog":
                result, cursor = MeteoclimaticCatalog(queue, claim, adapter).run()
            else:
                batch = adapter.download(claim.kind)
                result, cursor = ingestor.ingest(batch, after_chunk=after_chunk)
            queue.succeed(claim, result, cursor)
        return {"status": "succeeded", "kind": claim.kind, "result": result}
    except IngestionError as error:
        queue.fail(claim, error)
        return {"status": "paused" if error.pause else "retry", "code": error.code}
    finally:
        adapter.close()


def status(queue):
    with Session(queue.engine) as db:
        provider = db.scalar(select(Provider).where(Provider.code == queue.provider_code))
        if not provider:
            return {"status": "not_configured"}
        runtime = db.get(ProviderRuntime, provider.id)
        age = (
            max(0, (db_now(db) - runtime.newest_observed_at).total_seconds())
            if runtime.newest_observed_at
            else None
        )
        jobs = db.scalars(select(Job).where(Job.provider_id == provider.id)).all()
        runs = db.scalars(
            select(IngestionRun)
            .join(Job)
            .where(Job.provider_id == provider.id)
            .order_by(IngestionRun.started_at.desc())
            .limit(5)
        ).all()
        return {
            "provider": provider.code,
            "status": provider.status,
            "access_status": queue.access_status,
            "observation_age_seconds": age,
            "data_state": (
                "no_data"
                if age is None
                else "stale"
                if age > provider.capabilities.get("stale_after_seconds", 3600)
                else "fresh"
            ),
            "runtime": {
                key: getattr(runtime, key)
                for key in (
                    "day_calls",
                    "blocked_until",
                    "pause_reason",
                    "last_polled_at",
                    "last_new_data_at",
                    "newest_observed_at",
                )
            },
            "jobs": [
                {
                    "kind": job.kind,
                    "status": job.status,
                    "attempts": job.attempts,
                    "next_run_at": job.next_run_at,
                    "lease_until": job.lease_until,
                }
                for job in jobs
            ],
            "runs": [
                {
                    "started_at": run.started_at,
                    "status": run.status,
                    "error_code": run.error_code,
                    "result": run.result,
                }
                for run in runs
            ],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        choices=("current", "inventory", "catalog"),
        help="one due job; respects scheduling, leases and quotas",
    )
    parser.add_argument("--provider", choices=("aemet", "meteoclimatic"))
    parser.add_argument("--status", action="store_true", help="local database only, no HTTP")
    parser.add_argument("--resume", action="store_true", help="clear credential/contract pause")
    parser.add_argument("--max-runs", type=int, help="stop after this many claimed jobs")
    args = parser.parse_args()
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    try:
        settings = get_settings()
    except ValueError:
        emit(status="error", code="invalid_configuration")
        return 2
    queues = [
        Queue(get_engine(), settings, code)
        for code in ((args.provider,) if args.provider else ("aemet", "meteoclimatic"))
    ]
    # Preserve the original --once current/inventory behaviour unless a provider is selected.
    if args.once and not args.provider:
        queues = queues[:1]
    if args.resume and not args.provider:
        queues = queues[:1]
    stopped = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    count = 0
    while not stopped.is_set():
        try:
            if args.status:
                if args.provider:
                    emit(**status(queues[0]))
                else:
                    emit(providers=[status(queue) for queue in queues])
                return 0
            for queue in queues:
                queue.schedule()
            if args.resume:
                try:
                    queues[0].resume()
                except IngestionError as error:
                    emit(status="paused", code=error.code)
                    return 1
                emit(status="resumed")
                return 0
            claim = None
            for index in range(len(queues)):
                queue = queues[(count + index) % len(queues)]
                claim = queue.claim(args.once)
                if claim:
                    break
            report = {}
            if claim:
                try:
                    report = run_claim(queue, claim)
                    emit(provider=queue.provider_code, **report)
                except LeaseLost:
                    emit(status="lease_lost")
                except SQLAlchemyError:
                    raise
                except Exception:
                    queue.fail(claim, IngestionError("internal_error"))
                    emit(status="retry", code="internal_error")
                count += 1
            if args.once:
                if claim is None:
                    emit(status="not_due_or_paused")
                return 0 if claim and report.get("status") == "succeeded" else 1
            if args.max_runs and count >= args.max_runs:
                return 0
        except SQLAlchemyError:
            emit(status="retry", code="database_unavailable")
            if args.once or args.status or args.resume:
                return 1
        if stopped.wait(settings.worker_poll_seconds):
            break
    emit(status="stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

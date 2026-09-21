"""PostgreSQL scheduling, fencing and quotas; independent of HTTP and the API."""

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from meteocentro.config import Settings
from meteocentro.ingestion_errors import IngestionError, LeaseLost
from meteocentro.models import IngestionRun, Job, Provider, ProviderRuntime


def db_now(db: Session) -> datetime:
    return db.scalar(select(func.clock_timestamp())).astimezone(UTC)


@dataclass(frozen=True)
class Claim:
    job_id: uuid.UUID
    provider_id: uuid.UUID
    token: uuid.UUID
    run_id: uuid.UUID
    kind: str
    cursor: dict


class Queue:
    def __init__(self, engine, settings: Settings, provider_code="aemet"):
        if provider_code not in {"aemet", "meteoclimatic"}:
            raise ValueError("unsupported_provider")
        self.engine, self.settings, self.provider_code = engine, settings, provider_code

    def setting(self, name):
        return getattr(self.settings, f"{self.provider_code}_{name}")

    @property
    def access_status(self):
        if (
            self.provider_code == "meteoclimatic"
            and not self.settings.meteoclimatic_terms_reference
        ):
            return "pending_terms"
        return "verified" if self.setting("enabled") else "disabled"

    def schedule(self):
        with Session(self.engine) as db, db.begin():
            # Leadership is held for the entire scheduling transaction, never by the API.
            if not db.scalar(text("SELECT pg_try_advisory_xact_lock(746302001)")):
                return
            now = db_now(db)
            provider_id = db.scalar(
                insert(Provider)
                .values(
                    code=self.provider_code,
                    name="AEMET OpenData" if self.provider_code == "aemet" else "Meteoclimatic",
                    status=self.access_status,
                    capabilities={
                        "discover": True,
                        "current": True,
                        "daily_history": self.provider_code == "aemet",
                        "native_cadence_seconds": 3600 if self.provider_code == "aemet" else 900,
                        "stale_after_seconds": 5400 if self.provider_code == "aemet" else 2700,
                    },
                    terms_url=(
                        "https://www.aemet.es/es/nota_legal"
                        if self.provider_code == "aemet"
                        else "https://www.meteoclimatic.net/index/wp/cc_es.html"
                    ),
                    poll_interval_seconds=self.setting("poll_seconds"),
                    daily_call_budget=self.setting("daily_http_budget"),
                )
                .on_conflict_do_nothing(index_elements=[Provider.code])
                .returning(Provider.id)
            )
            if provider_id is None:
                provider_id = db.scalar(
                    select(Provider.id).where(Provider.code == self.provider_code)
                )
            db.execute(
                insert(ProviderRuntime)
                .values(
                    provider_id=provider_id,
                    day_start=now.replace(hour=0, minute=0, second=0, microsecond=0),
                    day_calls=0,
                    recent_calls=[],
                )
                .on_conflict_do_nothing()
            )
            state = db.scalar(
                select(ProviderRuntime)
                .where(ProviderRuntime.provider_id == provider_id)
                .with_for_update()
            )
            provider = db.get(Provider, provider_id)
            provider.poll_interval_seconds = self.setting("poll_seconds")
            provider.daily_call_budget = self.setting("daily_http_budget")
            provider.status = (
                self.access_status
                if self.access_status != "verified"
                else ("paused" if state.pause_reason else "verified")
            )
            if self.provider_code == "aemet":
                provider.capabilities = {
                    **provider.capabilities,
                    "credential_configured": bool(self.settings.aemet_api_key),
                }
            if self.provider_code == "meteoclimatic":
                provider.capabilities = {
                    **provider.capabilities,
                    "terms_reference": self.settings.meteoclimatic_terms_reference,
                }
            if provider.status != "verified":
                return
            products = [("current", 100, self.setting("poll_seconds"))]
            if self.provider_code == "aemet":
                products.append(("inventory", 20, 86400))
            else:
                products.append(("catalog", 20, 60))
            for kind, priority, interval in products:
                db.execute(
                    insert(Job)
                    .values(
                        kind=kind,
                        provider_id=provider_id,
                        status="pending",
                        next_run_at=now,
                        dedupe_key=f"{self.provider_code}:{kind}",
                        priority=priority,
                        interval_seconds=interval,
                    )
                    .on_conflict_do_update(
                        index_elements=[Job.dedupe_key],
                        set_={"interval_seconds": interval},
                        where=Job.interval_seconds != interval,
                    )
                )

    def claim(self, kind: str | None = None) -> Claim | None:
        with Session(self.engine) as db, db.begin():
            provider = db.scalar(select(Provider).where(Provider.code == self.provider_code))
            if (
                provider is None
                or provider.status != "verified"
                or self.access_status != "verified"
            ):
                return None
            state = db.scalar(
                select(ProviderRuntime)
                .where(ProviderRuntime.provider_id == provider.id)
                .with_for_update()
            )
            now = db_now(db)
            if state.pause_reason or (state.blocked_until and state.blocked_until > now):
                return None
            running = db.scalar(
                select(func.count())
                .select_from(Job)
                .where(
                    Job.provider_id == provider.id, Job.status == "running", Job.lease_until > now
                )
            )
            if running >= (self.settings.aemet_concurrency if self.provider_code == "aemet" else 1):
                return None
            query = select(Job).where(
                Job.provider_id == provider.id,
                or_(
                    (Job.status.in_(["pending", "retry"])) & (Job.next_run_at <= now),
                    (Job.status == "running") & (Job.lease_until <= now),
                ),
            )
            if kind:
                query = query.where(Job.kind == kind)
            job = db.scalar(
                query.order_by(Job.priority.desc(), Job.next_run_at, Job.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            if job.status == "running":
                db.execute(
                    update(IngestionRun)
                    .where(
                        IngestionRun.job_id == job.id,
                        IngestionRun.status == "running",
                    )
                    .values(status="abandoned", finished_at=now, error_code="lease_expired")
                )
            job.owner_token = uuid.uuid4()
            job.lease_until = now + timedelta(seconds=self.settings.worker_lease_seconds)
            job.status = "running"
            job.attempts += 1
            run = IngestionRun(
                job_id=job.id,
                started_at=now,
                status="running",
                owner_token=job.owner_token,
                result={},
            )
            db.add(run)
            db.flush()
            return Claim(job.id, provider.id, job.owner_token, run.id, job.kind, job.cursor or {})

    def fence(self, db: Session, claim: Claim) -> Job:
        job = db.scalar(select(Job).where(Job.id == claim.job_id).with_for_update())
        if (
            job.status != "running"
            or db.scalar(select(Provider.status).where(Provider.id == claim.provider_id))
            != "verified"
            or self.access_status != "verified"
            or job.owner_token != claim.token
            or job.lease_until <= db_now(db)
        ):
            raise LeaseLost()
        return job

    def heartbeat(self, claim: Claim):
        with Session(self.engine) as db, db.begin():
            job = self.fence(db, claim)
            job.lease_until = db_now(db) + timedelta(seconds=self.settings.worker_lease_seconds)

    def reserve_http(self, claim: Claim):
        error = None
        with Session(self.engine) as db, db.begin():
            # Same lock order everywhere: provider state before job.
            state = db.scalar(
                select(ProviderRuntime)
                .where(ProviderRuntime.provider_id == claim.provider_id)
                .with_for_update()
            )
            self.fence(db, claim)
            now = db_now(db)
            if state.pause_reason:
                raise IngestionError(state.pause_reason, pause=True)
            if state.blocked_until and state.blocked_until > now:
                raise IngestionError("provider_cooldown", retry_at=state.blocked_until)
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            if state.day_start < day:
                state.day_start, state.day_calls, state.history_calls = day, 0, 0
            recent = [
                datetime.fromisoformat(t)
                for t in state.recent_calls
                if datetime.fromisoformat(t) > now - timedelta(minutes=1)
            ]
            budget = self.setting("daily_http_budget")
            if claim.kind != "current":
                budget = max(0, budget - self.setting("current_reserve"))
            if (
                claim.kind == "history"
                and state.history_calls >= self.settings.history_daily_http_budget
            ):
                error = IngestionError("history_budget", retry_at=day + timedelta(days=1))
            elif state.day_calls >= budget:
                error = IngestionError("daily_budget", retry_at=day + timedelta(days=1))
            elif len(recent) >= self.setting("minute_http_budget"):
                error = IngestionError("minute_budget", retry_at=recent[0] + timedelta(minutes=1))
            else:
                recent.append(now)
                state.day_calls += 1
                if claim.kind == "history":
                    state.history_calls += 1
            state.recent_calls = [t.isoformat() for t in recent]
        if error:
            raise error

    def succeed(self, claim: Claim, result: dict, cursor: dict):
        with Session(self.engine) as db, db.begin():
            state = db.scalar(
                select(ProviderRuntime)
                .where(ProviderRuntime.provider_id == claim.provider_id)
                .with_for_update()
            )
            job = self.fence(db, claim)
            now = db_now(db)
            if claim.kind == "current":
                state.last_polled_at = now
            newest = result.get("newest_observed_at")
            if newest:
                instant = datetime.fromisoformat(newest)
                if state.newest_observed_at is None or instant > state.newest_observed_at:
                    state.newest_observed_at = instant
                    state.last_new_data_at = now
            run = db.get(IngestionRun, claim.run_id)
            run.status, run.finished_at, run.result = "succeeded", now, result
            job.status, job.attempts, job.cursor = (
                (
                    "completed"
                    if claim.kind in {"history", "verify"} and result.get("complete")
                    else "pending"
                ),
                0,
                cursor,
            )
            job.next_run_at = now + timedelta(seconds=job.interval_seconds)
            job.owner_token, job.lease_until = None, None

    def fail(self, claim: Claim, error: IngestionError):
        with Session(self.engine) as db, db.begin():
            state = db.scalar(
                select(ProviderRuntime)
                .where(ProviderRuntime.provider_id == claim.provider_id)
                .with_for_update()
            )
            job = self.fence(db, claim)
            now = db_now(db)
            if error.pause:
                state.pause_reason = error.code
                db.get(Provider, claim.provider_id).status = "paused"
                job.status = "paused"
            else:
                job.status = "retry"
                # At most three immediate retries; after that return to ordinary cadence.
                delay = (
                    min(300, 15 * 2 ** (job.attempts - 1)) + random.uniform(0, 5)
                    if job.attempts <= 3
                    else job.interval_seconds
                )
                job.next_run_at = max(now + timedelta(seconds=delay), error.retry_at or now)
                if job.attempts > 3:
                    job.attempts = 0
                if error.code in {"rate_limited", "minute_budget", "provider_cooldown"}:
                    state.blocked_until = job.next_run_at
            run = db.get(IngestionRun, claim.run_id)
            run.status, run.error_code, run.finished_at = "failed", error.code, now
            job.owner_token, job.lease_until = None, None

    def resume(self):
        if self.access_status != "verified":
            raise IngestionError(self.access_status, pause=True)
        with Session(self.engine) as db, db.begin():
            provider = db.scalar(select(Provider).where(Provider.code == self.provider_code))
            if provider is None:
                return
            state = db.scalar(
                select(ProviderRuntime)
                .where(ProviderRuntime.provider_id == provider.id)
                .with_for_update()
            )
            state.pause_reason = None
            provider.status = "verified"
            db.execute(
                update(Job)
                .where(Job.provider_id == provider.id, Job.status == "paused")
                .values(status="pending", next_run_at=db_now(db), attempts=0)
            )

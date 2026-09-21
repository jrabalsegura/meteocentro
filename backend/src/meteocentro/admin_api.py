"""Private management API. Responses explicitly select safe fields."""

from datetime import timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select

from meteocentro.administration import (
    admin_lock,
    audit,
    dismiss_duplicate,
    moderate,
    relink,
    review_location,
)
from meteocentro.auth import Admin, Db, require_admin
from meteocentro.catalog import excluded, validate_identity
from meteocentro.domain.eligibility import eligible_source_ids
from meteocentro.job_queue import db_now
from meteocentro.models import (
    AdminUser,
    AuditEvent,
    DailySummary,
    DuplicateCandidate,
    Exclusion,
    IdentityExclusion,
    IngestionRun,
    Job,
    Observation,
    Provider,
    ProviderRuntime,
    Station,
    StationSource,
)

router = APIRouter(
    prefix="/api/v1/admin", tags=["administration"], dependencies=[Depends(require_admin)]
)

# Only controlled error codes are exposed, never exception text or arbitrary stored errors.
ERRORS = {
    "pending_access",
    "pending_terms",
    "rate_limited",
    "minute_budget",
    "daily_budget",
    "history_budget",
    "provider_cooldown",
    "http_timeout",
    "http_transport",
    "product_unavailable",
    "internal_error",
    "lease_expired",
    "administrative_exclusion",
    "metadata_changed",
    "daily_metadata_changed",
    "robots_disallowed",
    "invalid_credentials",
}


def safe_error(code):
    return code if code in ERRORS else "provider_review_required" if code else None


def safe_result(result):
    keys = {
        "received",
        "valid",
        "new_sources",
        "updated_sources",
        "excluded",
        "invalid",
        "outside",
        "pending",
        "potential_duplicates",
        "location_review",
        "profiles_checked",
        "located",
        "review",
        "inserted",
        "revised",
        "unchanged",
        "complete",
        "found",
        "coverage_incomplete",
    }
    return {k: v for k, v in (result or {}).items() if k in keys and isinstance(v, (int, bool))}


def source_exclusion(db, source):
    entry = db.scalar(
        select(Exclusion).where(Exclusion.source_id == source.id, Exclusion.revoked_at.is_(None))
    )
    if entry is None:
        entry = db.scalar(
            select(IdentityExclusion).where(
                IdentityExclusion.provider_id == source.provider_id,
                IdentityExclusion.external_id == source.external_id,
                IdentityExclusion.revoked_at.is_(None),
            )
        )
    if entry is None:
        return None
    actor = getattr(entry, "actor_id", None)
    return {
        "reason": entry.reason,
        "created_at": entry.created_at,
        "actor": db.get(AdminUser, actor).username if actor else "comando local",
    }


def station_item(db, station):
    sources = db.scalars(
        select(StationSource)
        .where(StationSource.station_id == station.id)
        .order_by(StationSource.external_id)
    ).all()
    active = db.scalar(
        select(Exclusion).where(Exclusion.station_id == station.id, Exclusion.revoked_at.is_(None))
    )
    eligible = set(db.scalars(eligible_source_ids(station.id)))
    own = {source.id: source_exclusion(db, source) for source in sources}
    return {
        "id": station.id,
        "name": station.name,
        "province_code": station.province_code,
        "latitude": station.latitude,
        "longitude": station.longitude,
        "altitude_m": station.altitude_m,
        "status": "excluded"
        if active or station.moderation_status == "excluded"
        else station.moderation_status,
        "exclusion": {
            "reason": active.reason,
            "created_at": active.created_at,
            "actor": db.get(AdminUser, active.actor_id).username
            if active.actor_id
            else "comando local",
        }
        if active
        else None,
        "sources": [
            {
                "id": s.id,
                "provider": db.get(Provider, s.provider_id).code,
                "external_id": s.external_id,
                "status": s.status,
                "eligible": s.id in eligible,
                "excluded": bool(excluded(db, s)),
                "own_exclusion": bool(own[s.id]),
                "exclusion": own[s.id],
                "review_reason": s.review_reason,
                "latitude": s.latitude,
                "longitude": s.longitude,
                "proposed_location": s.source_metadata.get("proposed_location"),
                "precision": s.source_metadata.get("precision"),
                "last_seen_at": s.last_seen_at,
                "capabilities": {k: v for k, v in s.capabilities.items() if isinstance(v, bool)},
            }
            for s in sources
        ],
    }


@router.get("/stations")
def stations(
    db: Db,
    q: Annotated[str, Query(max_length=100)] = "",
    state: Literal["all", "review", "excluded"] = "all",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
):
    query = select(Station)
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(
            or_(
                Station.name.ilike(pattern),
                Station.id.in_(
                    select(StationSource.station_id).where(StationSource.external_id.ilike(pattern))
                ),
            )
        )
    if state == "review":
        query = query.where(
            or_(
                Station.moderation_status == "review",
                Station.id.in_(
                    select(StationSource.station_id).where(StationSource.review_reason.is_not(None))
                ),
            )
        )
    if state == "excluded":
        query = query.where(
            or_(
                Station.moderation_status == "excluded",
                Station.id.in_(select(Exclusion.station_id).where(Exclusion.revoked_at.is_(None))),
                Station.id.in_(
                    select(StationSource.station_id).where(
                        or_(
                            StationSource.id.in_(
                                select(Exclusion.source_id).where(Exclusion.revoked_at.is_(None))
                            ),
                            StationSource.id.in_(
                                select(StationSource.id)
                                .join(
                                    IdentityExclusion,
                                    (IdentityExclusion.provider_id == StationSource.provider_id)
                                    & (IdentityExclusion.external_id == StationSource.external_id),
                                )
                                .where(IdentityExclusion.revoked_at.is_(None))
                            ),
                        )
                    )
                ),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    return {
        "items": [
            station_item(db, s)
            for s in db.scalars(
                query.order_by(Station.name, Station.id).limit(limit).offset(offset)
            )
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/stations/{station_id}")
def station_detail(station_id: UUID, db: Db):
    station = db.get(Station, station_id)
    if station is None:
        raise HTTPException(404, detail={"code": "station_not_found"})
    return station_item(db, station)


@router.get("/stations/{station_id}/archive")
def archive(station_id: UUID, db: Db, offset: Annotated[int, Query(ge=0, le=100000)] = 0):
    station_detail(station_id, db)
    rows = db.scalars(
        select(Observation)
        .where(
            Observation.source_id.in_(
                select(StationSource.id).where(StationSource.station_id == station_id)
            )
        )
        .order_by(Observation.observed_at.desc(), Observation.id)
        .offset(offset)
        .limit(100)
    ).all()
    daily = db.scalars(
        select(DailySummary)
        .where(
            DailySummary.source_id.in_(
                select(StationSource.id).where(StationSource.station_id == station_id)
            )
        )
        .order_by(DailySummary.period_start.desc(), DailySummary.id)
        .offset(offset)
        .limit(100)
    ).all()
    return {
        "daily_summaries": [
            {
                "source_id": row.source_id,
                "period_start": row.period_start,
                "period_end": row.period_end,
                "period_basis": row.period_basis,
                "method": row.method,
                "metrics": row.metrics,
            }
            for row in daily
        ],
        "items": [
            {
                "source_id": row.source_id,
                "observed_at": row.observed_at,
                "period_start": row.period_start,
                "period_end": row.period_end,
                "product": row.product,
                "metrics": row.metrics,
            }
            for row in rows
        ],
        "offset": offset,
        "limit": 100,
    }


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Reason(Input):
    reason: str | None = Field(default=None, max_length=1000)


class Evidence(Input):
    evidence: str = Field(min_length=3, max_length=1000, pattern=r"\S")


def commit(db, operation):
    try:
        result = operation()
        db.commit()
        return result
    except ValueError as error:
        db.rollback()
        raise HTTPException(409, detail={"code": str(error)}) from None


@router.post("/stations/{station_id}/{action}")
def station_action(
    station_id: UUID, action: Literal["exclude", "restore"], body: Reason, db: Db, user: Admin
):
    return commit(
        db,
        lambda: moderate(
            db, user.id, station_id=station_id, restore=action == "restore", reason=body.reason
        ),
    )


@router.post("/sources/{source_id}/{action}")
def source_action(
    source_id: UUID, action: Literal["exclude", "restore"], body: Reason, db: Db, user: Admin
):
    return commit(
        db,
        lambda: moderate(
            db, user.id, source_id=source_id, restore=action == "restore", reason=body.reason
        ),
    )


class Link(Evidence):
    station_id: UUID


# Use distinct path depth to avoid matching the enum moderation route.
@router.post("/sources/{source_id}/review/link")
def link(source_id: UUID, body: Link, db: Db, user: Admin):
    return commit(db, lambda: relink(db, user.id, source_id, body.station_id, body.evidence))


@router.post("/sources/{source_id}/review/split")
def split(source_id: UUID, body: Evidence, db: Db, user: Admin):
    return commit(db, lambda: relink(db, user.id, source_id, None, body.evidence, split=True))


class Coordinates(Evidence):
    latitude: Decimal = Field(ge=-90, le=90, decimal_places=6)
    longitude: Decimal = Field(ge=-180, le=180, decimal_places=6)
    precision: Literal["minute", "exact"] = "minute"


@router.post("/sources/{source_id}/review/location")
def coordinates(source_id: UUID, body: Coordinates, db: Db, user: Admin):
    return commit(
        db,
        lambda: review_location(
            db, user.id, source_id, body.latitude, body.longitude, body.precision, body.evidence
        ),
    )


@router.get("/duplicates")
def duplicates(db: Db):
    rows = db.scalars(
        select(DuplicateCandidate)
        .where(DuplicateCandidate.status == "pending")
        .order_by(DuplicateCandidate.created_at)
        .limit(200)
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "source_id": r.source_id,
                "other_source_id": r.other_source_id,
                "source_name": db.get(StationSource, r.source_id).external_id,
                "other_source_name": db.get(StationSource, r.other_source_id).external_id,
                "distance_m": r.distance_m,
                "reason": r.reason,
            }
            for r in rows
        ]
    }


@router.post("/duplicates/{candidate_id}/distinct")
def distinct(candidate_id: UUID, body: Evidence, db: Db, user: Admin):
    return commit(db, lambda: dismiss_duplicate(db, user.id, candidate_id, body.evidence))


@router.get("/providers")
def providers(db: Db):
    from meteocentro.operations import snapshot

    now = db_now(db)
    return {
        "operations": snapshot(db),
        "items": [
            {
                "code": p.code,
                "name": p.name,
                "status": p.status,
                "capabilities": {
                    k: v for k, v in p.capabilities.items() if isinstance(v, (bool, int))
                },
                "credential": "no_requerida"
                if p.code == "meteoclimatic"
                else "falta_credencial"
                if r and r.pause_reason == "pending_access"
                else "configurada"
                if p.capabilities.get("credential_configured")
                else "gestionada_por_worker",
                "last_polled_at": r.last_polled_at if r else None,
                "observation_age_seconds": max(0, (now - r.newest_observed_at).total_seconds())
                if r and r.newest_observed_at
                else None,
                "pause_reason": safe_error(r.pause_reason) if r else None,
                "blocked_until": r.blocked_until if r else None,
                "day_calls": r.day_calls if r else 0,
                "daily_call_budget": p.daily_call_budget,
            }
            for p, r in db.execute(
                select(Provider, ProviderRuntime)
                .outerjoin(ProviderRuntime)
                .where(Provider.code.in_(["aemet", "meteoclimatic"]))
                .order_by(Provider.code)
            )
        ],
    }


@router.get("/operations")
def operations(db: Db):
    from meteocentro.operations import snapshot

    return snapshot(db)


def job_item(db, job):
    run = db.scalar(
        select(IngestionRun)
        .where(IngestionRun.job_id == job.id)
        .order_by(IngestionRun.started_at.desc())
        .limit(1)
    )
    return {
        "id": job.id,
        "provider": db.get(Provider, job.provider_id).code if job.provider_id else None,
        "source_id": job.source_id,
        "kind": job.kind,
        "status": job.status,
        "next_run_at": job.next_run_at,
        "attempts": job.attempts,
        "last_run": {
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "error_code": safe_error(run.error_code),
            "result": safe_result(run.result),
        }
        if run
        else None,
    }


@router.get("/jobs")
def jobs(db: Db, offset: Annotated[int, Query(ge=0, le=100000)] = 0):
    return {
        "items": [
            job_item(db, j)
            for j in db.scalars(
                select(Job).order_by(Job.next_run_at.desc(), Job.id).limit(100).offset(offset)
            )
        ],
        "offset": offset,
    }


@router.get("/audit")
def events(db: Db, offset: Annotated[int, Query(ge=0, le=100000)] = 0):
    return {
        "items": [
            {
                "id": e.id,
                "actor": name or "comando local",
                "action": e.action,
                "target_id": e.target_id,
                "occurred_at": e.occurred_at,
                "details": e.details,
            }
            for e, name in db.execute(
                select(AuditEvent, AdminUser.username)
                .outerjoin(AdminUser)
                .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id)
                .offset(offset)
                .limit(100)
            )
        ]
    }


def operational(db, provider):
    if (
        provider is None
        or provider.code not in {"aemet", "meteoclimatic"}
        or provider.status != "verified"
    ):
        raise ValueError("provider_not_available")
    runtime = db.scalar(
        select(ProviderRuntime).where(ProviderRuntime.provider_id == provider.id).with_for_update()
    )
    if not runtime or runtime.pause_reason:
        raise ValueError("provider_not_available")
    return runtime


def manual_limit(db, provider_id):
    recent = db.scalar(
        select(AuditEvent.id)
        .where(
            AuditEvent.action == "request_discovery",
            AuditEvent.target_id == provider_id,
            AuditEvent.occurred_at > db_now(db) - timedelta(minutes=15),
        )
        .limit(1)
    )
    if recent:
        raise ValueError("discovery_limited_15_minutes")


class Discover(Input):
    external_id: str | None = Field(default=None, min_length=1, max_length=28)


@router.post("/discovery/{provider_code}")
def discovery(
    provider_code: Literal["aemet", "meteoclimatic"], body: Discover, db: Db, user: Admin
):
    def operation():
        admin_lock(db)
        provider = db.scalar(select(Provider).where(Provider.code == provider_code))
        operational(db, provider)
        external_id = body.external_id
        if external_id:
            validate_identity(provider_code, external_id)
            key = f"verify:{provider_code}:{external_id}"
        else:
            key = f"{provider_code}:" + ("inventory" if provider_code == "aemet" else "current")
        job = db.scalar(select(Job).where(Job.dedupe_key == key).with_for_update())
        if job and job.status in {"running", "retry"}:
            return {"job": job_item(db, job), "already_active": True}
        if job and job.status == "cancelled":
            raise ValueError("restore_source_first")
        if job and job.status == "pending" and job.next_run_at <= db_now(db):
            return {"job": job_item(db, job), "already_active": True}
        manual_limit(db, provider.id)
        if not job:
            if not external_id:
                raise ValueError("worker_setup_required")
            source = db.scalar(
                select(StationSource).where(
                    StationSource.provider_id == provider.id,
                    StationSource.external_id == external_id,
                )
            )
            if source and excluded(db, source):
                raise ValueError("restore_source_first")
            job = Job(
                kind="verify",
                provider_id=provider.id,
                source_id=source.id if source else None,
                status="pending",
                next_run_at=db_now(db),
                dedupe_key=key,
                cursor={"external_id": external_id},
                priority=10,
                interval_seconds=86400,
            )
            db.add(job)
        else:
            job.status, job.next_run_at = "pending", db_now(db)
        audit(
            db, user.id, "request_discovery", "provider", provider.id, {"external_id": external_id}
        )
        db.flush()
        return {"job": job_item(db, job), "already_active": False}

    return commit(db, operation)


@router.post("/jobs/{job_id}/retry")
def retry(job_id: UUID, body: Input, db: Db, user: Admin):
    def operation():
        admin_lock(db)
        job = db.get(Job, job_id)
        if job is None:
            raise ValueError("job_not_found")
        runtime = operational(db, db.get(Provider, job.provider_id))
        job = db.scalar(
            select(Job)
            .where(Job.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job.status != "retry":
            raise ValueError("job_not_retryable")
        if job.next_run_at > db_now(db) or (
            runtime.blocked_until and runtime.blocked_until > db_now(db)
        ):
            raise ValueError("respect_retry_delay")
        if job.source_id and not db.scalar(
            eligible_source_ids().where(StationSource.id == job.source_id)
        ):
            raise ValueError("restore_source_first")
        job.status = "pending"
        audit(db, user.id, "retry_job", "job", job.id)
        return {"job": job_item(db, job)}

    return commit(db, operation)

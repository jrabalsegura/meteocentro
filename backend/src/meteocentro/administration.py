"""Local moderation transactions. No provider calls, no archive deletion."""

from sqlalchemy import or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from meteocentro.catalog import excluded, identity_lock, link_source, location
from meteocentro.domain.eligibility import eligible_source_ids
from meteocentro.job_queue import db_now
from meteocentro.models import (
    AuditEvent,
    CatalogVersion,
    DuplicateCandidate,
    Exclusion,
    IdentityExclusion,
    IngestionRun,
    Job,
    Station,
    StationLocationHistory,
    StationSource,
)


def admin_lock(db):
    # Also used by enqueue_history: no new individual job can miss cancellation.
    db.execute(text("SELECT pg_advisory_xact_lock(746306001)"))


def audit(db, actor, action, target_type, target_id, details=None):
    db.add(
        AuditEvent(
            actor_id=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details or {},
        )
    )
    version = db.scalar(
        insert(CatalogVersion)
        .values(id=1, version=1)
        .on_conflict_do_update(
            index_elements=[CatalogVersion.id], set_={"version": CatalogVersion.version + 1}
        )
        .returning(CatalogVersion.version)
    )
    return version


def lock_targets(db, station_id=None, source_id=None):
    admin_lock(db)
    source = db.get(StationSource, source_id) if source_id else None
    if source_id and source is None:
        raise ValueError("source_not_found")
    station_id = station_id or source.station_id
    if db.get(Station, station_id) is None:
        raise ValueError("station_not_found")
    sources = db.scalars(
        select(StationSource)
        .where(
            StationSource.id == source_id if source_id else StationSource.station_id == station_id
        )
        .order_by(StationSource.id)
    ).all()
    # Fenced workers acquire job -> catalogue -> identity -> station. Keep that order.
    jobs = db.scalars(
        select(Job)
        .where(or_(Job.source_id.in_([s.id for s in sources]), Job.kind == "verify"))
        .order_by(Job.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    db.execute(text("SELECT pg_advisory_xact_lock(746303001)"))
    for item in sources:
        identity_lock(db, item.provider_id, item.external_id)
    station = db.scalar(
        select(Station)
        .where(Station.id == station_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return station, sources, [j for j in jobs if j.source_id in {s.id for s in sources}]


def cancel_jobs(db, jobs):
    now = db_now(db)
    count = 0
    for job in jobs:
        if job.status in {"completed", "cancelled"}:
            continue
        db.execute(
            update(IngestionRun)
            .where(IngestionRun.job_id == job.id, IngestionRun.status == "running")
            .values(status="cancelled", finished_at=now, error_code="administrative_exclusion")
        )
        job.status, job.owner_token, job.lease_until = "cancelled", None, None
        job.cursor = {**(job.cursor or {}), "cancelled_by_exclusion": True}
        count += 1
    return count


def restore_jobs(db, jobs):
    count = 0
    for job in jobs:
        if job.status != "cancelled" or not (job.cursor or {}).get("cancelled_by_exclusion"):
            continue
        if not db.scalar(eligible_source_ids().where(StationSource.id == job.source_id)):
            continue
        job.status = "completed" if job.cursor.get("confirmed") else "pending"
        job.cursor = {k: v for k, v in job.cursor.items() if k != "cancelled_by_exclusion"}
        job.next_run_at, job.attempts = db_now(db), 0
        job.owner_token, job.lease_until = None, None
        count += 1
    return count


def moderate(db, actor, *, station_id=None, source_id=None, restore=False, reason=None):
    station, sources, jobs = lock_targets(db, station_id, source_id)
    criterion = (
        Exclusion.source_id == source_id if source_id else Exclusion.station_id == station.id
    )
    active = db.scalar(select(Exclusion).where(criterion, Exclusion.revoked_at.is_(None)))
    changed = False
    if restore:
        if active:
            active.revoked_at = db_now(db)
            changed = True
        # Older local tools may have used the moderation flag as well as a tombstone.
        if not source_id and station.moderation_status == "excluded":
            station.moderation_status = (
                "review" if any(s.review_reason for s in sources) else "active"
            )
            changed = True
        if source_id:
            source = sources[0]
            identity = db.scalar(
                select(IdentityExclusion).where(
                    IdentityExclusion.provider_id == source.provider_id,
                    IdentityExclusion.external_id == source.external_id,
                    IdentityExclusion.revoked_at.is_(None),
                )
            )
            if identity:
                identity.revoked_at = db_now(db)
                changed = True
        db.flush()
        count = restore_jobs(db, jobs)
    else:
        if not active:
            db.add(
                Exclusion(
                    station_id=None if source_id else station.id,
                    source_id=source_id,
                    actor_id=actor,
                    reason=reason,
                )
            )
            changed = True
        count = cancel_jobs(db, jobs)
    action = ("restore_" if restore else "exclude_") + ("source" if source_id else "station")
    version = None
    if changed or count:
        version = audit(
            db,
            actor,
            action,
            "source" if source_id else "station",
            source_id or station.id,
            {
                "reason": reason,
                "jobs": count,
                "sources": [str(s.id) for s in sources],
                "archive_preserved": True,
            },
        )
    db.flush()
    visible = list(db.scalars(eligible_source_ids(station.id)))
    return {
        "changed": changed,
        "jobs_changed": count,
        "catalog_version": version,
        "eligible_sources": [str(item) for item in visible],
        "warning": "El periodo excluido puede contener huecos." if restore else None,
    }


def relink(db, actor, source_id, target_id, evidence, *, split=False):
    station, sources, jobs = lock_targets(db, source_id=source_id)
    source = sources[0]
    if split:
        province, reason = location({"latitude": source.latitude, "longitude": source.longitude})
        source.review_reason = source.review_reason or reason
        target = Station(
            name=station.name,
            latitude=source.latitude,
            longitude=source.longitude,
            altitude_m=station.altitude_m,
            province_code=province,
            moderation_status="review" if source.review_reason else "active",
        )
        db.add(target)
        db.flush()
        target_id = target.id
    if target_id == station.id:
        raise ValueError("same_station")
    link_source(db, source_id, target_id, evidence, actor_id=actor)
    db.flush()
    if not split:
        members = list(
            db.scalars(select(StationSource.id).where(StationSource.station_id == target_id))
        )
        db.execute(
            update(DuplicateCandidate)
            .where(
                DuplicateCandidate.source_id.in_(members),
                DuplicateCandidate.other_source_id.in_(members),
                DuplicateCandidate.status == "pending",
            )
            .values(status="linked")
        )
        for member_id in members:
            member = db.get(StationSource, member_id)
            pending = db.scalar(
                select(DuplicateCandidate.id)
                .where(
                    or_(
                        DuplicateCandidate.source_id == member_id,
                        DuplicateCandidate.other_source_id == member_id,
                    ),
                    DuplicateCandidate.status == "pending",
                )
                .limit(1)
            )
            if member.review_reason == "potential_duplicate" and not pending:
                member.review_reason = (
                    "location_changed"
                    if member.source_metadata.get("proposed_location")
                    else location({"latitude": member.latitude, "longitude": member.longitude})[1]
                )
        db.flush()
        release_review(db, db.get(Station, target_id))
    # A link to an excluded station immediately cancels individual work as well.
    if excluded(db, source) or db.get(Station, target_id).moderation_status == "excluded":
        cancel_jobs(db, jobs)
    audit(
        db,
        actor,
        "split_source" if split else "review_link",
        "source",
        source_id,
        {"from": str(station.id), "to": str(target_id), "evidence": evidence},
    )
    return {"station_id": target_id}


def release_review(db, station):
    sources = db.scalars(select(StationSource).where(StationSource.station_id == station.id)).all()
    if station.moderation_status == "excluded":
        return
    station.moderation_status = "review" if any(s.review_reason for s in sources) else "active"


def review_location(db, actor, source_id, latitude, longitude, precision, evidence):
    station, sources, _ = lock_targets(db, source_id=source_id)
    source = sources[0]
    province, reason = location({"latitude": latitude, "longitude": longitude})
    if reason:
        raise ValueError("location_outside_scope")
    if station.latitude is not None and station.longitude is not None:
        db.add(
            StationLocationHistory(
                station_id=station.id,
                valid_from=station.created_at,
                latitude=station.latitude,
                longitude=station.longitude,
                altitude_m=station.altitude_m,
                evidence=evidence,
            )
        )
    source.latitude, source.longitude = latitude, longitude
    station.latitude, station.longitude, station.province_code = latitude, longitude, province
    source.source_metadata = {
        **source.source_metadata,
        "precision": precision,
        "location_method": "manual",
        "location_evidence": evidence,
        "verified_at": db_now(db).isoformat(),
        "proposed_location": None,
    }
    if source.review_reason != "potential_duplicate":
        source.review_reason = None
    db.flush()
    release_review(db, station)
    audit(
        db,
        actor,
        "approve_location",
        "source",
        source_id,
        {"latitude": str(latitude), "longitude": str(longitude), "evidence": evidence},
    )
    return {"status": station.moderation_status}


def dismiss_duplicate(db, actor, candidate_id, evidence):
    admin_lock(db)
    db.execute(text("SELECT pg_advisory_xact_lock(746303001)"))
    candidate = db.get(DuplicateCandidate, candidate_id)
    if candidate is None:
        raise ValueError("candidate_not_found")
    candidate.status = "distinct"
    db.flush()
    for source_id in (candidate.source_id, candidate.other_source_id):
        source = db.get(StationSource, source_id)
        station = db.scalar(
            select(Station).where(Station.id == source.station_id).with_for_update()
        )
        pending = db.scalar(
            select(DuplicateCandidate.id)
            .where(
                or_(
                    DuplicateCandidate.source_id == source_id,
                    DuplicateCandidate.other_source_id == source_id,
                ),
                DuplicateCandidate.status == "pending",
            )
            .limit(1)
        )
        if not pending and source.review_reason == "potential_duplicate":
            source.review_reason = (
                "location_changed"
                if source.source_metadata.get("proposed_location")
                else location({"latitude": source.latitude, "longitude": source.longitude})[1]
            )
        db.flush()
        release_review(db, station)
    audit(
        db, actor, "distinct_stations", "duplicate_candidate", candidate_id, {"evidence": evidence}
    )
    return {"status": "distinct"}

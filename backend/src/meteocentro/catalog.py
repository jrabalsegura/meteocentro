"""Shared discovery; never interprets proximity as proof of physical identity."""

import math
import re
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert

from meteocentro.aemet import folded, source_identity
from meteocentro.domain.provinces import classify_province
from meteocentro.job_queue import db_now
from meteocentro.models import (
    AuditEvent,
    DuplicateCandidate,
    Exclusion,
    IdentityExclusion,
    Station,
    StationLocationHistory,
    StationSource,
)

DUPLICATE_RADIUS_M = 1000


def identity_lock(db, provider_id, external_id):
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
        {"identity": f"{provider_id}:{external_id}"},
    )


def external_identity(provider, external_id):
    if provider == "aemet":
        return source_identity(external_id)
    return uuid5(NAMESPACE_URL, f"https://www.meteoclimatic.net/perfil/{external_id}")


def validate_identity(provider, external_id):
    pattern = r"[A-Za-z0-9]{1,20}" if provider == "aemet" else r"ES[A-Z0-9]{5,22}"
    if provider not in {"aemet", "meteoclimatic"} or not re.fullmatch(pattern, external_id):
        raise ValueError("invalid_identity")


def location(info):
    lat, lon = info.get("latitude"), info.get("longitude")
    if lat is None or lon is None:
        return None, "missing_coordinates"
    province = classify_province(float(lon), float(lat))
    if province is None:
        return None, "outside"
    # Province is approximate for minute coordinates; that uncertainty does not hide a station.
    return province, None


def excluded(db, source):
    direct = db.scalar(
        select(Exclusion.id)
        .where(
            Exclusion.revoked_at.is_(None),
            or_(Exclusion.station_id == source.station_id, Exclusion.source_id == source.id),
        )
        .limit(1)
    )
    return direct or db.scalar(
        select(IdentityExclusion.id).where(
            IdentityExclusion.provider_id == source.provider_id,
            IdentityExclusion.external_id == source.external_id,
            IdentityExclusion.revoked_at.is_(None),
        )
    )


def review_absences(db, provider_id, seen, fetched_at, counters):
    """Daily local review of the already downloaded current catalog; never deletes data."""
    db.execute(text("SELECT pg_advisory_xact_lock(746303001)"))
    sources = db.scalars(
        select(StationSource)
        .where(StationSource.provider_id == provider_id, StationSource.status == "enabled")
        .order_by(StationSource.id)
    ).all()
    for source in sources:
        db.scalar(select(Station).where(Station.id == source.station_id).with_for_update())
        if not source.capabilities.get("current") or excluded(db, source):
            continue
        metadata = dict(source.source_metadata)
        previous = metadata.get("catalog_checked_at")
        if previous and fetched_at - datetime.fromisoformat(previous) < timedelta(days=1):
            continue
        missing = source.external_id not in seen
        misses = metadata.get("consecutive_absent_reviews", 0) + 1 if missing else 0
        source.source_metadata = {
            **metadata,
            "catalog_checked_at": fetched_at.isoformat(),
            "consecutive_absent_reviews": misses,
        }
        if missing:
            counters["absent_sources"] += 1
        if misses >= 3 and source.review_reason in {None, "missing_in_feed"}:
            source.review_reason = "missing_in_feed"
            counters["absence_review"] += 1
        elif not missing and source.review_reason == "missing_in_feed":
            source.review_reason = None


def distance_m(lat1, lon1, lat2, lon2):
    a, b = math.radians(float(lat1)), math.radians(float(lat2))
    delta = math.radians(float(lon1) - float(lon2))
    h = math.sin((a - b) / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(delta / 2) ** 2
    return 12742000 * math.asin(min(1, math.sqrt(h)))


def suggest_duplicates(db, source, info, counters):
    if source.latitude is None or source.longitude is None:
        return
    nearby = db.execute(
        select(StationSource, Station)
        .join(Station)
        .where(
            StationSource.id != source.id,
            StationSource.station_id != source.station_id,
            StationSource.latitude.between(
                source.latitude - Decimal("0.04"), source.latitude + Decimal("0.04")
            ),
            StationSource.longitude.between(
                source.longitude - Decimal("0.05"), source.longitude + Decimal("0.05")
            ),
        )
    ).all()
    for other, station in nearby:
        if other.provider_id == source.provider_id and (
            station.moderation_status != "excluded" and not excluded(db, other)
        ):
            continue
        distance = distance_m(source.latitude, source.longitude, other.latitude, other.longitude)
        minute = "minute" in (info.get("precision"), other.source_metadata.get("precision"))
        name_match = folded(info["name"]) == folded(station.name)
        if distance > (DUPLICATE_RADIUS_M if minute else 250) and not (
            name_match and distance <= DUPLICATE_RADIUS_M
        ):
            continue
        first, second = sorted((source.id, other.id))
        result = db.execute(
            insert(DuplicateCandidate)
            .values(
                source_id=first,
                other_source_id=second,
                distance_m=round(distance, 2),
                reason="proximity_and_name" if name_match else "proximity",
            )
            .on_conflict_do_nothing(constraint="uq_duplicate_pair")
            .returning(DuplicateCandidate.id)
        )
        if result.scalar_one_or_none() is None:
            reviewed = db.scalar(
                select(DuplicateCandidate.status).where(
                    DuplicateCandidate.source_id == first,
                    DuplicateCandidate.other_source_id == second,
                )
            )
            if reviewed != "pending":
                continue
        source.review_reason = "potential_duplicate"
        db.get(Station, source.station_id).moderation_status = "review"
        counters["potential_duplicates"] += 1


def recheck_duplicate_radius(db, evidence):
    """Explicit local reconciliation; retain candidate history and all exclusions."""
    if not evidence.strip():
        raise ValueError("review_evidence_required")
    db.execute(text("SELECT pg_advisory_xact_lock(746303001)"))
    candidates = db.scalars(
        select(DuplicateCandidate).where(
            DuplicateCandidate.status == "pending",
            DuplicateCandidate.reason.in_(["proximity", "proximity_and_name"]),
        )
    ).all()
    affected = set()
    retired = 0
    for candidate in candidates:
        first = db.get(StationSource, candidate.source_id)
        second = db.get(StationSource, candidate.other_source_id)
        coordinates = (first.latitude, first.longitude, second.latitude, second.longitude)
        if None in coordinates or first.station_id == second.station_id:
            continue
        distance = distance_m(*coordinates)
        if distance <= DUPLICATE_RADIUS_M:
            continue
        candidate.status = "outside_radius"
        affected.update((first.id, second.id))
        retired += 1
        db.add(
            AuditEvent(
                action="duplicate_radius_recheck",
                target_type="duplicate_candidate",
                target_id=candidate.id,
                details={
                    "evidence": evidence,
                    "radius_m": DUPLICATE_RADIUS_M,
                    "distance_m": round(distance, 2),
                    "previous_status": "pending",
                },
            )
        )
    db.flush()
    return {
        "radius_m": DUPLICATE_RADIUS_M,
        "retired_pairs": retired,
        **_release_catalog_reviews(
            db,
            affected,
            evidence,
            "potential_duplicate",
            "duplicate_radius_source_review",
            {"radius_m": DUPLICATE_RADIUS_M},
        ),
    }


def recheck_boundary_locations(db, evidence):
    """Apply the explicitly accepted point-based province policy to old reviews."""
    if not evidence.strip():
        raise ValueError("review_evidence_required")
    db.execute(text("SELECT pg_advisory_xact_lock(746303001)"))
    sources = db.scalars(
        select(StationSource.id).where(StationSource.review_reason == "uncertain_boundary")
    ).all()
    return _release_catalog_reviews(
        db,
        sources,
        evidence,
        "uncertain_boundary",
        "boundary_location_review",
        {"location_policy": "reported_point"},
    )


def _release_catalog_reviews(db, affected, evidence, previous_reason, action, details):
    released, retained = [], []
    for source_id in sorted(affected):
        source = db.get(StationSource, source_id)
        if source.review_reason != previous_reason:
            continue
        identity_lock(db, source.provider_id, source.external_id)
        station = db.scalar(
            select(Station).where(Station.id == source.station_id).with_for_update()
        )
        unresolved = db.scalar(
            select(DuplicateCandidate.id)
            .where(
                or_(
                    DuplicateCandidate.source_id == source.id,
                    DuplicateCandidate.other_source_id == source.id,
                ),
                DuplicateCandidate.status != "outside_radius",
            )
            .limit(1)
        )
        siblings = db.scalars(
            select(StationSource.id).where(StationSource.station_id == station.id)
        ).all()
        meta = source.source_metadata
        if (
            unresolved
            or excluded(db, source)
            or source.status != "enabled"
            or station.moderation_status != "review"
            or len(siblings) != 1
            or meta.get("proposed_location")
            or not (meta.get("location_evidence") and meta.get("verified_at"))
        ):
            retained.append(source.external_id)
            continue
        province, reason = location(
            {
                "latitude": source.latitude,
                "longitude": source.longitude,
                "precision": meta.get("precision"),
            }
        )
        source.review_reason = reason
        station.province_code = province
        station.moderation_status = "review" if reason else "active"
        (retained if reason else released).append(source.external_id)
        db.add(
            AuditEvent(
                action=action,
                target_type="source",
                target_id=source.id,
                details={
                    "evidence": evidence,
                    **details,
                    "previous_reason": previous_reason,
                    "reason": reason,
                },
            )
        )
    return {
        "released": released,
        "retained": retained,
    }


def upsert_source(db, provider, info, counters, *, capability="current", now=None):
    """Caller holds a job fence (or is the explicitly invoked local catalogue command)."""
    validate_identity(provider.code, info["external_id"])
    # Serializes bounded catalog transactions across networks so two new neighbours
    # cannot both bypass duplicate review. Exclusion writers do not need this lock.
    db.execute(text("SELECT pg_advisory_xact_lock(746303001)"))
    identity_lock(db, provider.id, info["external_id"])
    tombstone = db.scalar(
        select(IdentityExclusion.id).where(
            IdentityExclusion.provider_id == provider.id,
            IdentityExclusion.external_id == info["external_id"],
            IdentityExclusion.revoked_at.is_(None),
        )
    )
    if tombstone:
        counters["excluded"] += 1
        return None
    source = db.scalar(
        select(StationSource).where(
            StationSource.provider_id == provider.id,
            StationSource.external_id == info["external_id"],
        )
    )
    now = now or db_now(db)
    if source:
        station = db.scalar(
            select(Station).where(Station.id == source.station_id).with_for_update()
        )
        if (
            excluded(db, source)
            or station.moderation_status == "excluded"
            or source.status != "enabled"
        ):
            counters["excluded"] += 1
            return None
        source.last_seen_at = max(source.last_seen_at, now)
        source.capabilities = {**source.capabilities, capability: True}
        source.source_metadata = {
            **source.source_metadata,
            "provider_name": info["name"],
            "provider_quality": info.get("qos"),
        }
        counters["updated_sources"] += 1
    else:
        station = None
    province, reason = location(info)
    if reason == "outside":
        counters["outside"] += 1
        if station:
            station.moderation_status = "review"
            source.review_reason = "outside"
        return None
    if source:
        changed = (
            capability in {"current", "manual_registration"}
            and info.get("latitude") is not None
            and (
                source.latitude is None
                or source.longitude is None
                or abs(source.latitude - info["latitude"]) > Decimal("0.002")
                or abs(source.longitude - info["longitude"]) > Decimal("0.002")
            )
        )
        if changed:
            proposal = {"latitude": str(info["latitude"]), "longitude": str(info["longitude"])}
            if source.source_metadata.get("proposed_location") != proposal:
                if station.latitude is not None and station.longitude is not None:
                    db.add(
                        StationLocationHistory(
                            station_id=station.id,
                            valid_from=station.created_at,
                            latitude=station.latitude,
                            longitude=station.longitude,
                            altitude_m=station.altitude_m,
                            evidence="Canonical position retained; unconfirmed relocation proposed",
                        )
                    )
                db.add(
                    AuditEvent(
                        action="location_review",
                        target_type="source",
                        target_id=source.id,
                        details=proposal,
                    )
                )
                source.source_metadata = {**source.source_metadata, "proposed_location": proposal}
            station.moderation_status = "review"
            if source.review_reason in {
                None,
                "missing_coordinates",
                "uncertain_boundary",
                "outside",
                "location_changed",
                "missing_in_feed",
            }:
                source.review_reason = "location_changed"
            counters["location_review"] += 1
        if reason:
            station.moderation_status = "review"
            if source.review_reason in {
                None,
                "missing_coordinates",
                "uncertain_boundary",
                "outside",
            }:
                source.review_reason = reason
    else:
        station = Station(
            name=info["name"],
            province_code=province,
            latitude=info.get("latitude"),
            longitude=info.get("longitude"),
            altitude_m=info.get("altitude_m"),
            moderation_status="review" if reason else "active",
        )
        db.add(station)
        db.flush()
        source = StationSource(
            id=external_identity(provider.code, info["external_id"]),
            provider_id=provider.id,
            station_id=station.id,
            external_id=info["external_id"],
            latitude=info.get("latitude"),
            longitude=info.get("longitude"),
            status="enabled",
            capabilities={capability: True},
            first_seen_at=now,
            last_seen_at=now,
            source_metadata={
                "provider_name": info["name"],
                "provider_quality": info.get("qos"),
                **info.get("location_metadata", {}),
            },
            review_reason=reason,
        )
        db.add(source)
        db.flush()
        suggest_duplicates(db, source, info, counters)
        counters["new_sources"] += 1
    if station.moderation_status != "active":
        counters["pending"] += 1
        counters["invalid"] += 1
        return None
    db.flush()
    return source


def link_source(db, source_id, station_id, evidence, actor_id=None):
    """Explicit, evidenced linking; all readings retain their original source identity."""
    if not evidence.strip():
        raise ValueError("link_evidence_required")
    # CLI linking shares the administrative lock: a source cannot join a station
    # between the exclusion service's job selection and its station lock.
    db.execute(text("SELECT pg_advisory_xact_lock(746306001)"))
    db.execute(text("SELECT pg_advisory_xact_lock(746303001)"))
    source = db.get(StationSource, source_id)
    if source is None or db.get(Station, station_id) is None:
        raise ValueError("unknown_link_target")
    identity_lock(db, source.provider_id, source.external_id)
    list(
        db.scalars(
            select(Station)
            .where(Station.id.in_([source.station_id, station_id]))
            .order_by(Station.id)
            .with_for_update()
        )
    )
    previous = source.station_id
    # Moving away from an excluded station must not evade that exclusion.
    old = db.get(Station, previous)
    if (excluded(db, source) or old.moderation_status == "excluded") and not db.scalar(
        select(Exclusion.id).where(Exclusion.source_id == source.id, Exclusion.revoked_at.is_(None))
    ):
        db.add(
            Exclusion(
                source_id=source.id,
                actor_id=actor_id,
                reason="Inherited on explicit source linking",
            )
        )
        db.flush()
    source.station_id = station_id
    db.add(
        AuditEvent(
            actor_id=actor_id,
            action="link_source",
            target_type="source",
            target_id=source.id,
            details={"from": str(previous), "to": str(station_id), "evidence": evidence},
        )
    )

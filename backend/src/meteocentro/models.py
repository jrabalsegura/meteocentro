import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def new_id() -> uuid.UUID:
    return uuid.uuid4()


class Provider(Base):
    __tablename__ = "providers"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), default="pending_access")
    capabilities: Mapped[dict] = mapped_column(JSONB, default=dict)
    terms_url: Mapped[str | None] = mapped_column(Text)
    poll_interval_seconds: Mapped[int | None] = mapped_column(Integer)
    daily_call_budget: Mapped[int | None] = mapped_column(Integer)


class Station(Base):
    __tablename__ = "stations"
    __table_args__ = (
        CheckConstraint(
            "moderation_status IN ('active','excluded','review')", name="station_moderation"
        ),
        CheckConstraint(
            "province_code IN ('05','19','28','40') OR province_code IS NULL",
            name="station_province",
        ),
        CheckConstraint("latitude BETWEEN -90 AND 90 OR latitude IS NULL", name="station_latitude"),
        CheckConstraint(
            "longitude BETWEEN -180 AND 180 OR longitude IS NULL", name="station_longitude"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    province_code: Mapped[str | None] = mapped_column(String(2))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    altitude_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    moderation_status: Mapped[str] = mapped_column(String(16), default="review")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StationSource(Base):
    __tablename__ = "station_sources"
    __table_args__ = (
        UniqueConstraint("provider_id", "external_id", name="uq_source_provider_external"),
        CheckConstraint("status IN ('enabled','paused','unavailable')", name="source_status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("providers.id"), index=True)
    station_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stations.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(200))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    status: Mapped[str] = mapped_column(String(16), default="unavailable")
    capabilities: Mapped[dict] = mapped_column(JSONB, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    source_metadata: Mapped[dict] = mapped_column(JSONB, server_default="{}", default=dict)
    review_reason: Mapped[str | None] = mapped_column(String(100))


class IdentityExclusion(Base):
    """Tombstone for an external ID, including IDs not yet discovered."""

    __tablename__ = "identity_exclusions"
    __table_args__ = (UniqueConstraint("provider_id", "external_id", name="uq_identity_exclusion"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("providers.id"))
    external_id: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DuplicateCandidate(Base):
    __tablename__ = "duplicate_candidates"
    __table_args__ = (
        UniqueConstraint("source_id", "other_source_id", name="uq_duplicate_pair"),
        CheckConstraint("source_id < other_source_id", name="duplicate_pair_order"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("station_sources.id"))
    other_source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("station_sources.id"))
    distance_m: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    reason: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StationLocationHistory(Base):
    __tablename__ = "station_location_history"
    __table_args__ = (
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="location_window"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    station_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stations.id"), index=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    altitude_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    evidence: Mapped[str | None] = mapped_column(Text)


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "product",
            "observed_at",
            "period_start",
            "period_end",
            "period_basis",
            name="uq_observation_semantic",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "(period_start IS NULL AND period_end IS NULL AND period_basis IS NULL) OR "
            "(period_start IS NOT NULL AND period_end IS NOT NULL AND "
            "period_basis IS NOT NULL AND period_end > period_start)",
            name="observation_period_pair",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("station_sources.id"), index=True)
    product: Mapped[str] = mapped_column(String(100))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_basis: Mapped[str | None] = mapped_column(String(100))
    metrics: Mapped[dict] = mapped_column(JSONB)
    quality: Mapped[dict] = mapped_column(JSONB, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64))
    normalizer_version: Mapped[str] = mapped_column(String(40))
    revised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ObservationRevision(Base):
    __tablename__ = "observation_revisions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    observation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("observations.id"), index=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    previous_metrics: Mapped[dict] = mapped_column(JSONB)
    previous_quality: Mapped[dict] = mapped_column(JSONB)
    previous_payload_hash: Mapped[str] = mapped_column(String(64))


class LatestObservation(Base):
    __tablename__ = "latest_observations"
    __table_args__ = (UniqueConstraint("source_id", "metric", name="uq_latest_source_metric"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("station_sources.id"))
    metric: Mapped[str] = mapped_column(String(80))
    observation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("observations.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DailySummary(Base):
    __tablename__ = "daily_summaries"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "product",
            "period_start",
            "period_end",
            "period_basis",
            "method",
            name="uq_daily_summary",
        ),
        CheckConstraint("period_end > period_start", name="daily_summary_window"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("station_sources.id"), index=True)
    product: Mapped[str] = mapped_column(String(100))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_basis: Mapped[str] = mapped_column(String(100))
    method: Mapped[str] = mapped_column(String(30))
    coverage: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    metrics: Mapped[dict] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HourlyAggregate(Base):
    __tablename__ = "hourly_aggregates"
    __table_args__ = (
        UniqueConstraint("source_id", "metric", "period_start", name="uq_hourly_aggregate"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("station_sources.id"))
    metric: Mapped[str] = mapped_column(String(80))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    minimum: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    maximum: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    mean: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    sample_count: Mapped[int] = mapped_column(Integer)
    coverage: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    method: Mapped[str] = mapped_column(String(40))


class Exclusion(Base):
    __tablename__ = "exclusions"
    __table_args__ = (
        CheckConstraint(
            "(station_id IS NOT NULL AND source_id IS NULL) OR "
            "(station_id IS NULL AND source_id IS NOT NULL)",
            name="exclusion_one_target",
        ),
        Index(
            "uq_active_station_exclusion",
            "station_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "uq_active_source_exclusion",
            "source_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    station_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stations.id"))
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("station_sources.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id"))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id"))
    action: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str] = mapped_column(String(60))
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_due", "status", "next_run_at"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(80))
    provider_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("providers.id"))
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("station_sources.id"))
    status: Mapped[str] = mapped_column(String(30))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor: Mapped[dict | None] = mapped_column(JSONB)
    dedupe_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    owner_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    priority: Mapped[int] = mapped_column(Integer, server_default="0")
    interval_seconds: Mapped[int] = mapped_column(Integer, server_default="900")


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30))
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100))
    owner_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class ProviderRuntime(Base):
    """Shared, durable budget. Every HTTP attempt is reserved before sending."""

    __tablename__ = "provider_runtime"
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("providers.id"), primary_key=True)
    day_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    day_calls: Mapped[int] = mapped_column(Integer, default=0)
    recent_calls: Mapped[list] = mapped_column(JSONB, default=list)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pause_reason: Mapped[str | None] = mapped_column(String(100))
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_new_data_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    newest_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProductMetadata(Base):
    __tablename__ = "product_metadata"
    __table_args__ = (
        UniqueConstraint("provider_id", "product", "payload_hash", name="uq_product_metadata"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("providers.id"))
    product: Mapped[str] = mapped_column(String(100))
    payload_hash: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fields: Mapped[list] = mapped_column(JSONB)


class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("admin_users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

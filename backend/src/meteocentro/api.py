from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from meteocentro.config import get_settings
from meteocentro.db import get_session
from meteocentro.domain.eligibility import eligible_source_ids, eligible_station_ids
from meteocentro.models import (
    DailySummary,
    LatestObservation,
    Observation,
    Provider,
    ProviderRuntime,
    Station,
    StationSource,
)
from meteocentro.schema import EXPECTED_REVISION


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_settings()  # Validate required configuration before serving requests.
    yield


app = FastAPI(
    title="Meteocentro API",
    version="0.1.0",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
    redoc_url=None,
    lifespan=lifespan,
)
DbSession = Annotated[Session, Depends(get_session)]


class StationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    province_code: str | None
    latitude: float | None
    longitude: float | None
    altitude_m: float | None
    freshness: Literal["fresh", "stale", "unknown", "historical_only"]


class SourceRead(BaseModel):
    id: UUID
    provider: str
    external_id: str
    status: str
    capabilities: dict
    source_url: str | None = None
    coordinate_precision: str | None = None


class StationDetail(StationRead):
    sources: list[SourceRead]


class StationPage(BaseModel):
    items: list[StationRead]
    total: int
    limit: int
    offset: int


class ObservationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    source_id: UUID
    product: str
    observed_at: datetime
    fetched_at: datetime
    period_start: datetime | None
    period_end: datetime | None
    period_basis: str | None
    metrics: dict
    quality: dict


class LatestRead(BaseModel):
    metric: str
    observation: ObservationRead


class LatestPage(BaseModel):
    items: list[LatestRead]


class ObservationPage(BaseModel):
    items: list[ObservationRead]
    limit: int
    offset: int
    next_offset: int | None


class SummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    source_id: UUID
    product: str
    period_start: datetime
    period_end: datetime
    period_basis: str
    method: str
    coverage: float | None
    metrics: dict
    fetched_at: datetime


class SummaryPage(BaseModel):
    items: list[SummaryRead]
    limit: int
    offset: int
    next_offset: int | None


@app.get("/health/live")
def live():
    return {"status": "alive"}


@app.get("/health/ready")
def ready(db: DbSession):
    try:
        db.execute(text("SELECT 1"))
        revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail={"code": "database_unavailable"}) from None
    if revision != EXPECTED_REVISION:
        raise HTTPException(status_code=503, detail={"code": "schema_mismatch"})
    return {"status": "ready", "schema_revision": revision}


@app.get("/api/v1/providers")
def providers(db: DbSession):
    """Operational availability without credentials, permission references or raw errors."""
    rows = db.execute(
        select(Provider, ProviderRuntime).outerjoin(ProviderRuntime).order_by(Provider.code)
    ).all()
    return {
        "items": [
            {
                "code": provider.code,
                "name": provider.name,
                "status": provider.status,
                "limitation": runtime.pause_reason if runtime else None,
                "last_polled_at": runtime.last_polled_at if runtime else None,
                "attribution": "Meteoclimatic y sus colaboradores"
                if provider.code == "meteoclimatic"
                else provider.name,
                "license_url": "https://creativecommons.org/licenses/by-nc-nd/3.0/"
                if provider.code == "meteoclimatic"
                else provider.terms_url,
            }
            for provider, runtime in rows
        ]
    }


def freshness(
    db: Session, station_id: UUID
) -> Literal["fresh", "stale", "unknown", "historical_only"]:
    sources = db.execute(
        select(StationSource, Provider)
        .join(Provider)
        .where(StationSource.id.in_(eligible_source_ids(station_id)))
    ).all()
    if sources and all(
        source.capabilities.get("daily_history") and not source.capabilities.get("current")
        for source, _ in sources
    ):
        return "historical_only"
    has_data = False
    for source, provider in sources:
        latest = db.scalar(
            select(func.max(LatestObservation.observed_at)).where(
                LatestObservation.source_id == source.id
            )
        )
        if latest is not None:
            has_data = True
            threshold = provider.capabilities.get("stale_after_seconds", 3600)
            if latest >= datetime.now(UTC) - timedelta(seconds=threshold):
                return "fresh"
    return "stale" if has_data else "unknown"


def station_read(db: Session, station: Station) -> StationRead:
    return StationRead(
        id=station.id,
        name=station.name,
        province_code=station.province_code,
        latitude=station.latitude,
        longitude=station.longitude,
        altitude_m=station.altitude_m,
        freshness=freshness(db, station.id),
    )


def eligible_station(db: Session, station_id: UUID) -> Station:
    station = db.scalar(
        select(Station).where(Station.id == station_id, Station.id.in_(eligible_station_ids()))
    )
    if station is None:
        raise HTTPException(status_code=404, detail={"code": "station_not_found"})
    return station


def checked_range(start: datetime, end: datetime, maximum: timedelta) -> tuple[datetime, datetime]:
    if start.utcoffset() is None or end.utcoffset() is None:
        raise HTTPException(status_code=422, detail={"code": "timezone_required"})
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if end <= start or end - start > maximum:
        raise HTTPException(status_code=422, detail={"code": "invalid_range"})
    return start, end


@app.get("/api/v1/stations", response_model=StationPage)
def list_stations(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    province: Annotated[str | None, Query(pattern=r"^(05|19|28|40)$")] = None,
):
    query = select(Station).where(Station.id.in_(eligible_station_ids()))
    if province:
        query = query.where(Station.province_code == province)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    stations = db.scalars(
        query.order_by(Station.name, Station.id).limit(limit).offset(offset)
    ).all()
    return StationPage(
        items=[station_read(db, station) for station in stations],
        total=total,
        limit=limit,
        offset=offset,
    )


@app.get("/api/v1/stations/{station_id}", response_model=StationDetail)
def station_detail(station_id: UUID, db: DbSession):
    station = eligible_station(db, station_id)
    sources = db.execute(
        select(StationSource, Provider.code)
        .join(Provider)
        .where(StationSource.id.in_(eligible_source_ids(station_id)))
        .order_by(Provider.code, StationSource.external_id)
    ).all()
    return StationDetail(
        **station_read(db, station).model_dump(),
        sources=[
            SourceRead(
                id=source.id,
                provider=code,
                external_id=source.external_id,
                status=source.status,
                capabilities=source.capabilities,
                source_url=f"https://www.meteoclimatic.net/perfil/{source.external_id}"
                if code == "meteoclimatic"
                else None,
                coordinate_precision=source.source_metadata.get("precision"),
            )
            for source, code in sources
        ],
    )


@app.get("/api/v1/stations/{station_id}/latest", response_model=LatestPage)
def latest_observations(station_id: UUID, db: DbSession):
    eligible_station(db, station_id)
    rows = db.execute(
        select(LatestObservation.metric, Observation)
        .join(
            Observation,
            (LatestObservation.observation_id == Observation.id)
            & (LatestObservation.source_id == Observation.source_id),
        )
        .where(LatestObservation.source_id.in_(eligible_source_ids(station_id)))
        .order_by(LatestObservation.metric, LatestObservation.source_id)
    ).all()
    return LatestPage(
        items=[
            LatestRead(metric=metric, observation=ObservationRead.model_validate(observation))
            for metric, observation in rows
        ]
    )


@app.get("/api/v1/stations/{station_id}/observations", response_model=ObservationPage)
def observations(
    station_id: UUID,
    db: DbSession,
    start: datetime,
    end: datetime,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
):
    eligible_station(db, station_id)
    start, end = checked_range(start, end, timedelta(days=31))
    rows = db.scalars(
        select(Observation)
        .where(
            Observation.source_id.in_(eligible_source_ids(station_id)),
            Observation.observed_at >= start,
            Observation.observed_at < end,
        )
        .order_by(Observation.observed_at, Observation.id)
        .limit(limit + 1)
        .offset(offset)
    ).all()
    has_more = len(rows) > limit
    return ObservationPage(
        items=[ObservationRead.model_validate(row) for row in rows[:limit]],
        limit=limit,
        offset=offset,
        next_offset=offset + limit if has_more else None,
    )


@app.get("/api/v1/stations/{station_id}/daily-summaries", response_model=SummaryPage)
def daily_summaries(
    station_id: UUID,
    db: DbSession,
    start: datetime,
    end: datetime,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
):
    eligible_station(db, station_id)
    start, end = checked_range(start, end, timedelta(days=366))
    rows = db.scalars(
        select(DailySummary)
        .where(
            DailySummary.source_id.in_(eligible_source_ids(station_id)),
            DailySummary.period_start >= start,
            DailySummary.period_start < end,
        )
        .order_by(DailySummary.period_start, DailySummary.id)
        .limit(limit + 1)
        .offset(offset)
    ).all()
    has_more = len(rows) > limit
    return SummaryPage(
        items=[SummaryRead.model_validate(row) for row in rows[:limit]],
        limit=limit,
        offset=offset,
        next_offset=offset + limit if has_more else None,
    )

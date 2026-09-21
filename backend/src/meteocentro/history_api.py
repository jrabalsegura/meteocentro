"""Public historical reads share eligibility with the current map."""

import csv
import io
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import Date, cast, exists, func, select
from sqlalchemy.orm import Session

from meteocentro.config import get_settings
from meteocentro.db import get_session
from meteocentro.domain.eligibility import eligible_source_ids
from meteocentro.history import (
    MADRID,
    VERSION,
    aggregate,
    channel_for,
    channels,
    civil_window,
    valid_value,
)
from meteocentro.models import (
    AggregateDirtyDay,
    DailySummary,
    HourlyAggregate,
    Observation,
    Provider,
    Station,
    StationLocationHistory,
    StationSource,
)

router = APIRouter(prefix="/api/v1")
Db = Annotated[Session, Depends(get_session)]
Metric = Literal[
    "temperature",
    "humidity",
    "pressure_station",
    "pressure_sea_level",
    "wind_speed",
    "wind_direction",
    "wind_gust",
    "rain",
    "rain_daily",
    "rain_rate",
]
Resolution = Literal["raw", "hour", "day"]


def selected_source(db, station_id, source_id):
    query = (
        select(StationSource, Provider)
        .join(Provider)
        .where(StationSource.id.in_(eligible_source_ids(station_id)))
    )
    if source_id:
        query = query.where(StationSource.id == source_id)
    rows = db.execute(query).all()
    if not rows:
        raise HTTPException(404, detail={"code": "station_or_source_not_found"})
    if len(rows) != 1:
        raise HTTPException(
            422, detail={"code": "select_source", "sources": [str(s.id) for s, _ in rows]}
        )
    return rows[0]


def checked_range(start, end, days):
    if start.utcoffset() is None or end.utcoffset() is None:
        raise HTTPException(422, detail={"code": "timezone_required"})
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if end <= start or end - start > timedelta(days=days):
        raise HTTPException(422, detail={"code": "invalid_range", "maximum_days": days})
    return start, end


def provenance(source, provider):
    return {
        "source_id": str(source.id),
        "provider": provider.code,
        "external_id": source.external_id,
        "attribution": provider.name,
        "export_allowed": provider.code == "aemet" and provider.status in {"verified", "paused"},
    }


def availability(db, source, metric):
    raw = db.execute(
        select(func.min(Observation.observed_at), func.max(Observation.observed_at)).where(
            Observation.source_id == source.id, Observation.metrics.has_key(metric)
        )
    ).one()
    daily = db.execute(
        select(func.min(DailySummary.period_start), func.max(DailySummary.period_end)).where(
            DailySummary.source_id == source.id, DailySummary.metrics.has_key(metric)
        )
    ).one()
    hourly = db.execute(
        select(func.min(HourlyAggregate.period_start), func.max(HourlyAggregate.period_end)).where(
            HourlyAggregate.source_id == source.id, HourlyAggregate.metric == metric
        )
    ).one()
    dirty = db.scalar(
        select(func.count())
        .select_from(AggregateDirtyDay)
        .where(AggregateDirtyDay.source_id == source.id)
    )
    return {
        "raw": {"first": raw[0], "last": raw[1]},
        "hour": {"first": hourly[0], "last": hourly[1]},
        "day": {"first": daily[0], "last": daily[1]},
        "pending_days": dirty,
    }


def raw_page(db, source, start, end, metric, limit, offset):
    rows = db.scalars(
        select(Observation)
        .where(
            Observation.source_id == source.id,
            Observation.observed_at >= start,
            Observation.observed_at < end,
            Observation.metrics.has_key(metric),
        )
        .order_by(Observation.observed_at, Observation.id)
        .limit(limit + 1)
        .offset(offset)
    ).all()
    items = []
    for row in rows[:limit]:
        measurement = row.metrics[metric]
        channel, description = channel_for(row, metric, measurement)
        items.append(
            {
                **description,
                "channel": channel,
                "time": row.observed_at.astimezone(UTC),
                "value": valid_value(measurement),
                "original": measurement,
                "quality": row.quality,
                "fetched_at": row.fetched_at,
                "period_start": row.period_start,
                "period_end": row.period_end,
                "period_basis": measurement.get("period_basis") or row.period_basis,
                "aggregation_method": "source_observation",
                "coverage": None,
            }
        )
    return items, offset + limit if len(rows) > limit else None


def summary_item(row, metric):
    return {
        **row.metrics[metric],
        "time": row.period_start.astimezone(UTC),
        "period_start": row.period_start,
        "period_end": row.period_end,
        "period_basis": row.period_basis,
        "aggregation_method": row.method,
        "channel": row.channel,
        "product": row.product,
        "provisional": row.provisional,
        "provenance": row.provenance,
        "fetched_at": row.fetched_at,
    }


def daily_items(db, source, start, end, metric, method):
    query = select(DailySummary).where(
        DailySummary.source_id == source.id,
        DailySummary.period_start >= start,
        DailySummary.period_start < end,
        DailySummary.metrics.has_key(metric),
    )
    if method != "all":
        query = query.where(DailySummary.method == (VERSION if method == "local" else "provider"))
    return [
        summary_item(row, metric)
        for row in db.scalars(query.order_by(DailySummary.period_start, DailySummary.channel)).all()
    ]


def pending_summary():
    return exists(
        select(AggregateDirtyDay.source_id).where(
            AggregateDirtyDay.source_id == DailySummary.source_id,
            AggregateDirtyDay.day
            == cast(func.timezone("Europe/Madrid", DailySummary.period_start), Date),
        )
    )


def plotted_value(item):
    if item.get("total") is not None:
        return item["total"]
    if item.get("mean") is not None:
        return item["mean"]
    return item.get("maximum")


@router.get("/stations/{station_id}/series")
def series(
    station_id: UUID,
    db: Db,
    start: Annotated[datetime, Query(alias="from")],
    end: Annotated[datetime, Query(alias="to")],
    metric: Metric = "temperature",
    source: UUID | None = None,
    resolution: Resolution = "raw",
    limit: Annotated[int, Query(ge=1, le=10000)] = 10000,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
):
    origin, provider = selected_source(db, station_id, source)
    start, end = checked_range(start, end, 31 if resolution == "raw" else 366)
    next_offset = None
    if resolution == "raw":
        items, next_offset = raw_page(db, origin, start, end, metric, limit, offset)
    elif resolution == "hour":
        rows = db.scalars(
            select(HourlyAggregate)
            .where(
                HourlyAggregate.source_id == origin.id,
                HourlyAggregate.metric == metric,
                HourlyAggregate.period_start >= start,
                HourlyAggregate.period_start < end,
            )
            .order_by(HourlyAggregate.period_start, HourlyAggregate.channel)
            .limit(limit + 1)
            .offset(offset)
        ).all()
        items = [
            {
                **r.stats,
                "channel": r.channel,
                "time": r.period_start.astimezone(UTC),
                "period_start": r.period_start,
                "period_end": r.period_end,
                "period_basis": "UTC_hour",
            }
            for r in rows[:limit]
        ]
        next_offset = offset + limit if len(rows) > limit else None
    else:
        # Deliberately only local aggregates; imported diaries are exposed in /daily.
        rows = daily_items(db, origin, start, end, metric, "local")
        items = rows[offset : offset + limit]
        next_offset = offset + limit if len(rows) > offset + limit else None
    cadence = origin.capabilities.get("native_cadence_seconds") or provider.capabilities.get(
        "native_cadence_seconds"
    )
    groups = defaultdict(list)
    for item in items:
        item["value"] = item.get("value") if resolution == "raw" else plotted_value(item)
        previous = groups[item["channel"]][-1] if groups[item["channel"]] else None
        expected = 3600 if resolution == "hour" else 90000 if resolution == "day" else cadence
        item["break_before"] = (
            previous is None
            or (not expected)
            or (item["time"] - previous["time"]).total_seconds() > expected * 1.5
            or item.get("coverage", 1) == 0
        )
        groups[item["channel"]].append(item)
    locations = db.scalars(
        select(StationLocationHistory)
        .where(
            StationLocationHistory.station_id == station_id, StationLocationHistory.valid_from < end
        )
        .order_by(StationLocationHistory.valid_from)
    ).all()
    for points in groups.values():
        for previous, item in zip(points, points[1:], strict=False):
            if any(previous["time"] < r.valid_from <= item["time"] for r in locations):
                item["break_before"] = True
                item["location_change"] = True
    coverage = []
    if resolution == "raw" and isinstance(cadence, (int, float)) and 1 <= cadence <= 3600:
        coverage_rows = db.scalars(
            select(Observation)
            .where(
                Observation.source_id == origin.id,
                Observation.metrics.has_key(metric),
                Observation.observed_at >= start - timedelta(seconds=cadence),
                Observation.observed_at <= end + timedelta(seconds=cadence),
            )
            .order_by(Observation.observed_at)
            .limit(10001)
        ).all()
        if len(coverage_rows) <= 10000:
            for channel, (description, samples) in channels(coverage_rows).items():
                if description["metric"] == metric:
                    coverage.append(
                        {"channel": channel, **aggregate(samples, start, end, cadence, description)}
                    )
    return {
        **provenance(origin, provider),
        "coverage": coverage,
        "metric": metric,
        "resolution": resolution,
        "items": items,
        "next_offset": next_offset,
        "limit": limit,
        "offset": offset,
        "availability": availability(db, origin, metric),
        "cadence_seconds": cadence,
        "events": [
            {
                "time": r.valid_from,
                "kind": "location",
                "latitude": r.latitude,
                "longitude": r.longitude,
                "altitude_m": r.altitude_m,
            }
            for r in locations
        ],
        "notice": "Sin datos disponibles" if not items else None,
    }


def rollup(items, resolution, start, end):
    groups = defaultdict(list)
    for item in items:
        local = (
            item["time"].astimezone(MADRID)
            if item["period_basis"] == "Europe/Madrid"
            else item["time"]
        )
        bucket = local.strftime("%Y-%m" if resolution == "month" else "%Y")
        groups[
            (
                bucket,
                item["channel"],
                item["period_basis"],
                item["aggregation_method"],
                item["product"],
            )
        ].append(item)
    result = []
    for (bucket, channel, basis, method, product), rows in sorted(groups.items()):
        low = [r for r in rows if r.get("minimum") is not None]
        high = [r for r in rows if r.get("maximum") is not None]
        sums = [r["total"] for r in rows if r.get("total") is not None]
        # Unknown provider coverage remains unknown. Never invent temporal weights.
        weights = [r for r in rows if r.get("mean") is not None and r.get("covered_seconds", 0) > 0]
        covered = sum(r.get("covered_seconds") or 0 for r in rows)
        y = int(bucket[:4])
        m = int(bucket[5:7]) if resolution == "month" else 1
        a = datetime(y, m, 1, tzinfo=MADRID if basis == "Europe/Madrid" else UTC)
        b = (
            datetime(y + 1, 1, 1, tzinfo=a.tzinfo)
            if m == 12 or resolution == "year"
            else datetime(y, m + 1, 1, tzinfo=a.tzinfo)
        )
        seconds = (b.astimezone(UTC) - a.astimezone(UTC)).total_seconds()
        coverage = covered / seconds if method == VERSION else None
        result.append(
            {
                "bucket": bucket,
                "channel": channel,
                "product": product,
                "period_basis": basis,
                "aggregation_method": method,
                "unit": rows[0].get("unit"),
                "period_start": a,
                "period_end": b,
                "days_available": len(rows),
                "first": min(r["time"] for r in rows),
                "last": max(r["period_end"] for r in rows),
                "minimum": min((r["minimum"] for r in low), default=None),
                "maximum": max((r["maximum"] for r in high), default=None),
                "minimum_at": min(low, key=lambda r: r["minimum"]).get("minimum_at")
                if low
                else None,
                "maximum_at": max(high, key=lambda r: r["maximum"]).get("maximum_at")
                if high
                else None,
                "mean": sum(r["mean"] * r["covered_seconds"] for r in weights)
                / sum(r["covered_seconds"] for r in weights)
                if weights and rows[0].get("metric") != "wind_direction"
                else None,
                "total": sum(sums) if sums else None,
                "coverage": coverage,
                "partial": coverage is None or coverage < 1,
                "provisional": any(r["provisional"] for r in rows),
                "requested_from": start,
                "requested_to": end,
            }
        )
    return result


@router.get("/stations/{station_id}/daily")
def daily(
    station_id: UUID,
    db: Db,
    start: Annotated[datetime, Query(alias="from")],
    end: Annotated[datetime, Query(alias="to")],
    metric: Metric = "temperature",
    source: UUID | None = None,
    method: Literal["all", "local", "provider"] = "all",
    resolution: Literal["day", "month", "year"] = "day",
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
):
    origin, provider = selected_source(db, station_id, source)
    start, end = checked_range(start, end, 366 if resolution == "day" else 3660)
    items = daily_items(db, origin, start, end, metric, method)
    if resolution != "day":
        items = rollup(items, resolution, start, end)
    return {
        **provenance(origin, provider),
        "items": items[offset : offset + limit],
        "next_offset": offset + limit if len(items) > offset + limit else None,
        "availability": availability(db, origin, metric),
        "resolution": resolution,
    }


@router.get("/stations/{station_id}/records")
def records(station_id: UUID, db: Db, metric: Metric = "temperature", source: UUID | None = None):
    origin, provider = selected_source(db, station_id, source)
    # Per-channel extrema via PostgreSQL, not a Python scan of the raw archive.
    from sqlalchemy import Float, cast

    result = []
    groups = db.execute(
        select(
            DailySummary.channel,
            DailySummary.method,
            DailySummary.period_basis,
            func.min(DailySummary.period_start),
            func.max(DailySummary.period_end),
            func.count(),
            func.max(DailySummary.metrics[metric]["unit"].as_string()),
        )
        .where(DailySummary.source_id == origin.id, DailySummary.metrics.has_key(metric))
        .group_by(DailySummary.channel, DailySummary.method, DailySummary.period_basis)
    ).all()
    for channel, method, basis, first, last, days, unit in groups:
        extremes = {}
        for field, descending in [("minimum", False), ("maximum", True), ("total", True)]:
            value = cast(DailySummary.metrics[metric][field].as_string(), Float)
            row = db.scalar(
                select(DailySummary)
                .where(
                    DailySummary.source_id == origin.id,
                    DailySummary.channel == channel,
                    DailySummary.method == method,
                    DailySummary.period_basis == basis,
                    value.is_not(None),
                    DailySummary.provisional.is_(False),
                    ~pending_summary(),
                    (
                        cast(DailySummary.metrics[metric]["coverage"].as_string(), Float)
                        >= get_settings().history_coverage_threshold
                    )
                    | (method == "provider"),
                )
                .order_by(value.desc() if descending else value.asc(), DailySummary.period_start)
                .limit(1)
            )
            extremes[field] = (
                {
                    "value": row.metrics[metric][field],
                    "period_start": row.period_start,
                    "period_end": row.period_end,
                    "observed_at": row.metrics[metric].get(field + "_at"),
                    "coverage": row.metrics[metric].get("coverage"),
                }
                if row
                else None
            )
        result.append(
            {
                "channel": channel,
                "unit": unit,
                "aggregation_method": method,
                "period_basis": basis,
                "first": first,
                "last": last,
                "days_available": days,
                **extremes,
            }
        )
    return {
        **provenance(origin, provider),
        "items": result,
        "availability": availability(db, origin, metric),
        "coverage_threshold": get_settings().history_coverage_threshold,
        "notice": (
            "Extremos del archivo disponible; diarios del proveedor con cobertura desconocida."
        ),
    }


def csv_cell(value):
    from numbers import Number

    if isinstance(value, Number):
        return value
    value = "" if value is None else str(value)
    return (
        "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else value
    )


@router.get("/stations/{station_id}/export.csv")
def export_csv(
    station_id: UUID,
    db: Db,
    start: Annotated[datetime, Query(alias="from")],
    end: Annotated[datetime, Query(alias="to")],
    metric: Metric = "temperature",
    source: UUID | None = None,
    resolution: Literal["raw", "day"] = "raw",
):
    origin, provider = selected_source(db, station_id, source)
    if not provenance(origin, provider)["export_allowed"]:
        raise HTTPException(403, detail={"code": "export_not_authorized"})
    start, end = checked_range(start, end, 31 if resolution == "raw" else 366)
    items, next_offset = (
        raw_page(db, origin, start, end, metric, 10000, 0)
        if resolution == "raw"
        else (daily_items(db, origin, start, end, metric, "all"), None)
    )
    if next_offset or len(items) > 10000:
        raise HTTPException(422, detail={"code": "export_too_large"})
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    columns = [
        "provider",
        "external_id",
        "metric",
        "time",
        "value",
        "unit",
        "minimum",
        "maximum",
        "mean",
        "total",
        "period_start",
        "period_end",
        "period_basis",
        "aggregation_method",
        "coverage",
        "quality",
        "fetched_at",
    ]
    writer.writerow(columns)
    for item in items:
        row = {**provenance(origin, provider), "metric": metric, **item}
        writer.writerow([csv_cell(row.get(key)) for key in columns])
    # Revalidate before publishing even when a summary was already computed.
    selected_source(db, station_id, source)
    return Response(
        output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=meteocentro.csv"},
    )


@router.get("/daily")
def network_daily(
    db: Db,
    day: date,
    metric: Literal["temperature", "rain", "humidity", "wind_gust"] = "temperature",
    province: Literal["05", "19", "28", "40"] | None = None,
    network: Literal["aemet", "meteoclimatic"] | None = None,
):
    start, end = civil_window(day)
    query = (
        select(DailySummary, StationSource, Provider, Station, pending_summary())
        .join(StationSource, StationSource.id == DailySummary.source_id)
        .join(Provider)
        .join(Station, Station.id == StationSource.station_id)
        .where(
            DailySummary.source_id.in_(eligible_source_ids()),
            DailySummary.period_start == start,
            DailySummary.period_basis == "Europe/Madrid",
            DailySummary.method == VERSION,
            DailySummary.metrics.has_key(metric),
        )
    )
    if province:
        query = query.where(Station.province_code == province)
    if network:
        query = query.where(Provider.code == network)
    rows = db.execute(query.limit(5001)).all()
    truncated = len(rows) > 5000
    items = []
    for row, origin, provider, station, pending in rows[:5000]:
        item = summary_item(row, metric)
        items.append(
            {
                **item,
                **provenance(origin, provider),
                "station_id": str(station.id),
                "name": station.name,
                "latitude": station.latitude,
                "longitude": station.longitude,
                "pending_recalculation": pending,
                "eligible": not pending
                and (item.get("coverage") or 0) >= get_settings().history_coverage_threshold,
            }
        )
    # Select separate semantic populations. Same cutoff is mandatory for provisional days.
    groups = defaultdict(list)
    for item in items:
        if item["eligible"]:
            key = (
                item["period_end"],
                item.get("unit"),
                item.get("kind"),
                item.get("input_period_basis"),
                item["aggregation_method"],
                item.get("normalizer_version"),
                item.get("product"),
            )
            groups[key].append(item)
    comparisons = []
    for population in groups.values():
        for field in ["total"] if metric == "rain" else ["minimum", "maximum"]:
            usable = [r for r in population if r.get(field) is not None]
            if usable and not truncated:
                extreme = (min if field == "minimum" else max)(usable, key=lambda r: r[field])
                comparisons.append({"field": field, "population": len(usable), "extreme": extreme})
    return {
        "items": items,
        "extremes": comparisons,
        "period_start": start,
        "period_end": end,
        "coverage_threshold": get_settings().history_coverage_threshold,
        "truncated": truncated,
        "notice": "Grupos con igual periodo, método, unidad y corte; totales parciales señalados.",
    }

"""Bounded public projections. No provider requests, caches or per-station queries."""

from collections import Counter, defaultdict
from datetime import UTC, datetime
from math import isfinite
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from meteocentro.db import get_session
from meteocentro.domain.eligibility import eligible_source_ids, eligible_station_ids
from meteocentro.history import MADRID, VERSION, aggregate, channels, civil_window
from meteocentro.models import (
    DailySummary,
    LatestObservation,
    Observation,
    Provider,
    Station,
    StationSource,
)

router = APIRouter(prefix="/api/v1")
Metric = Literal["temperature", "humidity", "wind_speed", "wind_gust", "rain", "rain_rate"]
Freshness = Literal["all", "fresh", "stale", "unknown", "historical_only"]
PREFERENCE = {"aemet": 0, "meteoclimatic": 1}
MAX_STATIONS = 5000


def source_read(source, provider):
    return {
        "id": str(source.id),
        "provider": provider.code,
        "external_id": source.external_id,
        "provider_status": provider.status,
        "coordinate_precision": source.source_metadata.get("precision"),
        "source_url": f"https://www.meteoclimatic.net/perfil/{source.external_id}"
        if provider.code == "meteoclimatic"
        else "https://www.aemet.es/es/eltiempo/observacion/ultimosdatos"
        if provider.code == "aemet"
        else None,
        "attribution": "Meteoclimatic y sus colaboradores"
        if provider.code == "meteoclimatic"
        else provider.name,
        "capabilities": source.capabilities,
    }


def value_read(metric, observation, source, provider, now):
    raw = observation.metrics.get(metric, {})
    value = raw.get("value")
    try:
        value = float(value) if value is not None else None
    except (TypeError, ValueError):
        value = None
    flags = raw.get("plausibility_flags", [])
    if value is not None and (not isfinite(value) or flags):
        value = None
    unit = raw.get("unit", "")
    if unit == "m/s":
        value = value * 3.6 if value is not None else None
        unit = "km/h"
    age = (now - observation.observed_at).total_seconds()
    threshold = provider.capabilities.get(
        "stale_after_seconds", 5400 if provider.code == "aemet" else 2700
    )
    # Future clocks are not recent observations.
    status = "unknown" if value is None or age < 0 else "fresh" if age <= threshold else "stale"
    direction = observation.metrics.get("wind_direction", {})
    direction_value = direction.get("value")
    if direction_value is not None and not direction.get("plausibility_flags"):
        direction_value = float(direction_value)
        if not isfinite(direction_value) or not 0 <= direction_value <= 360:
            direction_value = None
    else:
        direction_value = None
    return {
        "metric": metric,
        "value": value,
        "unit": unit,
        "kind": raw.get("kind"),
        "period_start": observation.period_start,
        "period_end": observation.period_end,
        "period_basis": raw.get("period_basis") or observation.period_basis,
        "observed_at": observation.observed_at,
        "fetched_at": observation.fetched_at,
        "age_seconds": max(0, int(age)),
        "stale_after_seconds": threshold,
        "freshness": status,
        "source_id": str(source.id),
        "provider": provider.code,
        "external_id": source.external_id,
        "product": observation.product,
        "direction_degrees": direction_value if metric.startswith("wind_") else None,
        "flags": flags,
    }


def read_rows(db, candidates, metric=None):
    """One statement also rechecks eligibility on every origin in the projection."""
    latest_join = LatestObservation.source_id == StationSource.id
    if metric:
        latest_join = and_(latest_join, LatestObservation.metric == metric)
    return db.execute(
        select(Station, StationSource, Provider, LatestObservation.metric, Observation)
        .join(StationSource, StationSource.station_id == Station.id)
        .join(Provider, Provider.id == StationSource.provider_id)
        .outerjoin(LatestObservation, latest_join)
        .outerjoin(
            Observation,
            and_(
                Observation.id == LatestObservation.observation_id,
                Observation.observed_at == LatestObservation.observed_at,
                Observation.source_id == StationSource.id,
            ),
        )
        .where(Station.id.in_(candidates), StationSource.id.in_(eligible_source_ids()))
        .order_by(Station.name, Station.id, Provider.code, StationSource.external_id)
    ).all()


def project(rows, now, networks=()):
    stations = {}
    for station, source, provider, metric, observation in rows:
        if networks and provider.code not in networks:
            continue
        item = stations.setdefault(
            str(station.id),
            {
                "id": str(station.id),
                "name": station.name,
                "municipality": source.source_metadata.get("municipality"),
                "province_code": station.province_code,
                "latitude": float(station.latitude) if station.latitude is not None else None,
                "longitude": float(station.longitude) if station.longitude is not None else None,
                "altitude_m": float(station.altitude_m) if station.altitude_m is not None else None,
                "sources": {},
                "values": [],
            },
        )
        if item["municipality"] is None:
            item["municipality"] = source.source_metadata.get("municipality")
        item["sources"][str(source.id)] = source_read(source, provider)
        if observation is not None:
            item["values"].append(value_read(metric, observation, source, provider, now))
    for item in stations.values():
        item["sources"] = sorted(
            item["sources"].values(),
            key=lambda s: (PREFERENCE.get(s["provider"], 99), s["external_id"]),
        )
        values = sorted(
            item.pop("values"),
            key=lambda v: (
                {"fresh": 0, "stale": 1, "unknown": 2}[v["freshness"]],
                PREFERENCE.get(v["provider"], 99),
                -v["observed_at"].timestamp(),
                v["source_id"],
            ),
        )
        item["reading"] = next((v for v in values if v["value"] is not None), None)
        item["readings"] = values
        item["fallback"] = bool(
            item["reading"] and item["reading"]["source_id"] != item["sources"][0]["id"]
        )
        item["freshness"] = (
            item["reading"]["freshness"]
            if item["reading"]
            else (
                "historical_only"
                if all(
                    s["capabilities"].get("daily_history") and not s["capabilities"].get("current")
                    for s in item["sources"]
                )
                else "unknown"
            )
        )
    return list(stations.values())


def comparisons(items, truncated):
    readings = [(s, s["reading"]) for s in items if s["freshness"] == "fresh"]
    groups = {
        (v["unit"], v["kind"], v["period_basis"], v["period_start"], v["period_end"])
        for _, v in readings
    }
    comparable = not truncated and len(groups) == 1 and len(readings) >= 2

    def extreme(which):
        station, value = which(readings, key=lambda row: row[1]["value"])
        return {"station_id": station["id"], "name": station["name"], "reading": value}

    return {
        "comparable": comparable,
        "eligible": len(readings),
        "population": len(items),
        "period_groups": len(groups),
        "reason": "limited_population"
        if truncated
        else "different_periods"
        if len(groups) > 1
        else "insufficient_recent_data"
        if len(readings) < 2
        else None,
        "minimum": extreme(min) if comparable else None,
        "maximum": extreme(max) if comparable else None,
        "observed_from": min((v["observed_at"] for _, v in readings), default=None),
        "observed_to": max((v["observed_at"] for _, v in readings), default=None),
    }


DAY_METRICS = {
    "temperature": ("temperature_daily_min", "temperature_daily_max"),
    "humidity": ("humidity_daily_min", "humidity_daily_max"),
    "wind_speed": (None, "wind_speed_daily_max"),
}


def km_h(value, unit):
    return value * 3.6 if value is not None and unit == "m/s" else value


def day_extremes(db, items, metric, now):
    """Today's min/max per listed station, from the same source as its reading.

    Same rule as the station card: a provider-reported daily value of today wins;
    otherwise our hourly civil-day summary, extended by the current reading of that
    source. Never mixes networks, ambiguous channels or earlier days. Two queries total.
    """
    if metric not in DAY_METRICS:
        return
    start, _ = civil_window(now.astimezone(MADRID).date())
    readings = {i["reading"]["source_id"]: i for i in items if i["reading"]}
    ids = [UUID(source_id) for source_id in readings]
    for item in items:
        item["day"] = None
    if not readings:
        return
    reported_names = [name for name in DAY_METRICS[metric] if name]
    reported = {}
    for source_id, name, observation in db.execute(
        select(LatestObservation.source_id, LatestObservation.metric, Observation)
        .join(
            Observation,
            and_(
                Observation.id == LatestObservation.observation_id,
                Observation.observed_at == LatestObservation.observed_at,
            ),
        )
        .where(
            LatestObservation.source_id.in_(ids),
            LatestObservation.metric.in_(reported_names),
            LatestObservation.observed_at >= start,
            LatestObservation.observed_at <= now,
        )
    ):
        raw = observation.metrics.get(name, {})
        value = raw.get("value")
        if value is not None and not raw.get("plausibility_flags"):
            reported[(str(source_id), name)] = km_h(float(value), raw.get("unit"))
    archived = defaultdict(list)
    for row in db.scalars(
        select(DailySummary).where(
            DailySummary.source_id.in_(ids),
            DailySummary.period_start == start,
            DailySummary.method == VERSION,
            DailySummary.metrics.has_key(metric),
        )
    ):
        stats = row.metrics[metric]
        if stats.get("kind") in {"instant", "interval_mean"}:
            archived[str(row.source_id)].append(stats)
    for source_id, item in readings.items():
        reading = item["reading"]
        # Ambiguous channels are never combined silently.
        stats = archived[source_id][0] if len(archived[source_id]) == 1 else None
        current = reading["value"] if reading["observed_at"] >= start else None
        result = {"provider": reading["provider"], "coverage": None, "partial": True}
        for field, name in zip(("minimum", "maximum"), DAY_METRICS[metric], strict=True):
            value, at, origin = None, None, None
            if name and (source_id, name) in reported:
                value, origin = reported[(source_id, name)], "reported"
                at = stats.get(f"{field}_at") if stats else None
            elif stats and stats.get(field) is not None:
                value = km_h(float(stats[field]), stats.get("unit"))
                at, origin = stats.get(f"{field}_at"), "archive"
            # The current reading only extends an existing archive, never replaces it.
            if current is not None and origin == "archive":
                if current < value if field == "minimum" else current > value:
                    value, at = current, reading["observed_at"].isoformat()
            result[field] = {"value": value, "at": at, "origin": origin}
        if stats:
            result["coverage"], result["partial"] = stats.get("coverage"), stats.get("partial")
        item["day"] = result


@router.get("/map")
def map_data(
    db: Annotated[Session, Depends(get_session)],
    metric: Metric = "temperature",
    freshness: Freshness = "all",
    province: Annotated[list[Literal["05", "19", "28", "40"]] | None, Query()] = None,
    network: Annotated[list[Literal["aemet", "meteoclimatic"]] | None, Query()] = None,
    bbox: Annotated[str | None, Query(max_length=120)] = None,
    q: Annotated[str, Query(max_length=120)] = "",
    limit: Annotated[int, Query(ge=1, le=5000)] = 2000,
):
    candidates = select(Station.id).where(Station.id.in_(eligible_station_ids()))
    if province:
        candidates = candidates.where(Station.province_code.in_(province))
    if network:
        candidates = candidates.where(
            Station.id.in_(
                select(StationSource.station_id)
                .join(Provider)
                .where(StationSource.id.in_(eligible_source_ids()), Provider.code.in_(network))
            )
        )
    if bbox:
        try:
            west, south, east, north = [float(x) for x in bbox.split(",")]
            assert all(isfinite(x) for x in (west, south, east, north))
            assert -180 <= west < east <= 180 and -90 <= south < north <= 90
        except (ValueError, AssertionError):
            raise HTTPException(422, detail={"code": "invalid_bbox"}) from None
        candidates = candidates.where(
            Station.longitude.between(west, east), Station.latitude.between(south, north)
        )
    if q.strip():
        # contains(autoescape) treats % and _ as text, never as wildcard input.
        candidates = candidates.where(
            or_(
                Station.name.icontains(q.strip(), autoescape=True),
                Station.id.in_(
                    select(StationSource.station_id).where(
                        StationSource.id.in_(eligible_source_ids()),
                        or_(
                            StationSource.external_id.icontains(q.strip(), autoescape=True),
                            StationSource.source_metadata["municipality"]
                            .as_string()
                            .icontains(q.strip(), autoescape=True),
                        ),
                    )
                ),
            )
        )
    candidates = candidates.order_by(Station.name, Station.id).limit(MAX_STATIONS + 1)
    now = datetime.now(UTC)
    items = project(read_rows(db, candidates, metric), now, network or ())
    capped = len(items) > MAX_STATIONS
    counts = dict(Counter(s["freshness"] for s in items[:MAX_STATIONS]))
    filtered = [
        s for s in items[:MAX_STATIONS] if freshness == "all" or s["freshness"] == freshness
    ]
    truncated = capped or len(filtered) > limit
    displayed = filtered[:limit]
    for item in displayed:
        item.pop("readings")
    day_extremes(db, displayed, metric, now)
    return {
        "items": displayed,
        "total": len(filtered),
        "registered": sum(counts.values()),
        "counts": counts,
        "limit": limit,
        "truncated": truncated,
        "total_is_lower_bound": capped,
        "metric": metric,
        "generated_at": now,
        "extremes": comparisons(filtered, truncated),
        "selection_policy": "recent_usable_then_aemet_meteoclimatic_then_latest",
    }


@router.get("/stations/{station_id}/current")
def current(station_id: UUID, db: Annotated[Session, Depends(get_session)]):
    candidates = select(Station.id).where(Station.id == station_id)
    now = datetime.now(UTC)
    items = project(read_rows(db, candidates), now)
    if not items:
        raise HTTPException(404, detail={"code": "station_not_found"})
    return {**items[0], "generated_at": now, "day_summaries": day_summaries(db, station_id, now)}


def day_summaries(db, station_id, now):
    """Today's local archive, by source/channel; no provider calls or cached diaries.

    Reported provider counters/extremes remain in readings with their own basis.
    Never sum those counters or mix sources to fill gaps in a civil day.
    """
    start, _ = civil_window(now.astimezone(MADRID).date())
    if now <= start:
        return []
    rows = db.execute(
        select(Observation, StationSource, Provider)
        .join(StationSource, Observation.source_id == StationSource.id)
        .join(Provider, StationSource.provider_id == Provider.id)
        .where(
            StationSource.id.in_(eligible_source_ids(station_id)),
            Observation.observed_at >= start,
            Observation.observed_at <= now,
            or_(Observation.metrics.has_key("temperature"), Observation.metrics.has_key("rain")),
        )
        .order_by(Observation.observed_at)
    ).all()
    origins = {}
    for observation, source, provider in rows:
        if source.id not in origins:
            origins[source.id] = (source, provider, [])
        origins[source.id][2].append(observation)
    summaries = []
    for source, provider, observations in origins.values():
        cadence = source.capabilities.get("native_cadence_seconds") or provider.capabilities.get(
            "native_cadence_seconds"
        )
        if not isinstance(cadence, (int, float)) or not 1 <= cadence <= 3600:
            continue
        for channel, (description, samples) in channels(observations).items():
            metric, kind = description["metric"], description["kind"]
            if not (
                (metric == "temperature" and kind == "instant")
                or (metric == "rain" and kind == "interval_total")
            ):
                continue
            summaries.append(
                {
                    **aggregate(samples, start, now, cadence, description),
                    "channel": channel,
                    "source_id": str(source.id),
                    "provider": provider.code,
                    "external_id": source.external_id,
                    "period_start": start,
                    "period_end": now,
                    "period_basis": "Europe/Madrid",
                    "observed_at": max(row.observed_at for row, _ in samples),
                }
            )
    return summaries

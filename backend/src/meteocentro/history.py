"""Versioned, conservative aggregation. Never derive intraday points from daily data."""

from datetime import UTC, date, datetime, time, timedelta
from math import atan2, cos, degrees, hypot, radians, sin
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from meteocentro.aemet import digest
from meteocentro.domain.eligibility import eligible_source_ids
from meteocentro.models import (
    AggregateDirtyDay,
    DailySummary,
    HourlyAggregate,
    Observation,
    Provider,
    Station,
    StationSource,
)

MADRID = ZoneInfo("Europe/Madrid")
VERSION = "local-v1"
INVALID = {"outside_physical_range", "invalid", "invalid_value", "rejected"}


def civil_window(day: date):
    return (
        datetime.combine(day, time(), MADRID).astimezone(UTC),
        datetime.combine(day + timedelta(days=1), time(), MADRID).astimezone(UTC),
    )


def valid_value(measurement):
    from math import isfinite

    if INVALID.intersection(measurement.get("plausibility_flags", [])) or measurement.get(
        "provider_quality"
    ) in {"invalid", "rejected"}:
        return None
    value = measurement.get("value")
    try:
        return float(value) if value is not None and isfinite(float(value)) else None
    except (ValueError, TypeError):
        return None


def channel_for(observation, name, measurement):
    description = {
        "product": observation.product,
        "metric": name,
        "unit": measurement.get("unit"),
        "kind": measurement.get("kind"),
        "input_period_basis": measurement.get("period_basis") or observation.period_basis,
        "normalizer_version": observation.normalizer_version,
        "interval_seconds": (
            observation.period_end.astimezone(UTC) - observation.period_start.astimezone(UTC)
        ).total_seconds()
        if observation.period_start
        and observation.period_end
        and measurement.get("kind")
        in {"interval_mean", "interval_total", "interval_maximum", "rolling_total"}
        else None,
    }
    return digest(description), description


def channels(observations):
    result = {}
    for observation in observations:
        for name, measurement in observation.metrics.items():
            key, description = channel_for(observation, name, measurement)
            if key not in result:
                result[key] = (description, [])
            result[key][1].append((observation, measurement))
    return result


def aggregate(samples, start, end, cadence, description):
    """Forward sample hold <= native cadence; explicit intervals keep their duration.

    Counter deltas require documented bounds, monotonic values and adjacent readings.
    Overlapping interval totals are all rejected, rather than picking a sensor silently.
    """
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    # psycopg can return Europe/Madrid ZoneInfo. Subtraction in that zone uses
    # wall time across DST; normalize every input before any interval arithmetic.
    samples = [
        (
            SimpleNamespace(
                id=r.id,
                observed_at=r.observed_at.astimezone(UTC),
                period_start=r.period_start.astimezone(UTC) if r.period_start else None,
                period_end=r.period_end.astimezone(UTC) if r.period_end else None,
                quality=r.quality,
                metrics=r.metrics,
            ),
            m,
        )
        for r, m in samples
    ]
    kind, metric = description["kind"], description["metric"]
    ordered = sorted(samples, key=lambda pair: (pair[0].observed_at, str(pair[0].id)))
    segments, flags = [], set()
    for index, (row, measurement) in enumerate(ordered):
        flags.update(measurement.get("plausibility_flags", []))
        value = valid_value(measurement)
        if value is None:
            flags.add("missing_or_invalid_samples")
            continue
        a, b = row.period_start, row.period_end
        if kind in {"rolling_total", "daily_minimum", "daily_maximum"}:
            flags.add("not_aggregatable")
            continue
        if kind == "daily_counter":
            if (
                a is None
                or b is None
                or description["input_period_basis"] == "provider_day_timezone_unknown"
            ):
                flags.add("unknown_counter_window")
                continue
            if index == 0:
                if not row.quality.get("counter_base_zero"):
                    flags.add("unknown_counter_base")
                    continue
                left, right, increment = a, row.observed_at, value
            else:
                previous, previous_metric = ordered[index - 1]
                old = valid_value(previous_metric)
                if (previous.period_start, previous.period_end) != (a, b):
                    flags.add("counter_period_reset")
                    if not row.quality.get("counter_base_zero"):
                        continue
                    left, right, increment = a, row.observed_at, value
                elif old is None or value < old:
                    flags.add("counter_descent_or_null")
                    continue
                else:
                    left, right, increment = previous.observed_at, row.observed_at, value - old
            if right - left > timedelta(seconds=cadence * 1.5):
                flags.add("counter_gap")
                continue
            a, b, value = left, right, increment
        elif kind in {"interval_total", "interval_mean", "interval_maximum"}:
            if a is None or b is None:
                flags.add("missing_interval")
                continue
        else:
            a = row.observed_at
            b = (
                min(a + timedelta(seconds=cadence), ordered[index + 1][0].observed_at)
                if index + 1 < len(ordered)
                else a + timedelta(seconds=cadence)
            )
        if b <= start or a >= end or b <= a:
            continue
        if kind in {"interval_total", "daily_counter"} and (a < start or b > end):
            flags.add("cross_boundary_total")
            continue  # An interval total cannot be apportioned without finer observations.
        if metric == "wind_direction":
            speed = valid_value(row.metrics.get("wind_speed", {}))
            if speed is None or speed <= 0:
                flags.add("calm_or_missing_speed")
                continue
        else:
            speed = 1
        segments.append((max(a, start), min(b, end), value, row.observed_at, speed))
    # Duplicate instants/products and overlapping intervals must not double coverage.
    conflicts = set()
    for i, seg in enumerate(segments):
        for j in range(i - 1, -1, -1):
            other = segments[j]
            if other[1] <= seg[0]:
                continue
            if other[0] < seg[1] and seg[0] < other[1]:
                conflicts.update((i, j))
    if conflicts:
        flags.add("overlapping_intervals")
        segments = [s for i, s in enumerate(segments) if i not in conflicts]
    seconds = sum((b - a).total_seconds() for a, b, *_ in segments)
    values = [(v, t) for _, _, v, t, _ in segments]
    low = min(values, default=(None, None), key=lambda v: v[0])
    high = max(values, default=(None, None), key=lambda v: v[0])
    total_kind = kind in {"interval_total", "daily_counter"}
    mean = None
    if seconds and not total_kind and kind != "interval_maximum":
        if metric == "wind_direction":
            x = sum(
                cos(radians(v)) * speed * (b - a).total_seconds() for a, b, v, _, speed in segments
            )
            y = sum(
                sin(radians(v)) * speed * (b - a).total_seconds() for a, b, v, _, speed in segments
            )
            mean = degrees(atan2(y, x)) % 360 if hypot(x, y) > 1e-8 else None
        else:
            mean = sum(v * (b - a).total_seconds() for a, b, v, _, _ in segments) / seconds
    coverage = seconds / (end - start).total_seconds()
    return {
        **description,
        "minimum": low[0],
        "maximum": high[0],
        "mean": mean,
        "minimum_at": low[1].isoformat() if low[1] else None,
        "maximum_at": high[1].isoformat() if high[1] else None,
        "total": sum(v for v, _ in values) if values and total_kind else None,
        "sample_count": len(values),
        "covered_seconds": seconds,
        "coverage": coverage,
        "partial": coverage < 1,
        "flags": sorted(flags),
        "aggregation_method": VERSION,
        "mean_method": "speed_weighted_vector"
        if metric == "wind_direction"
        else "time_weighted_bounded_hold",
        "cadence_seconds": cadence,
    }


def rebuild_day(db, source, day, *, now=None):
    now = now or datetime.now(UTC)
    start, end = civil_window(day)
    cutoff = min(end, now.replace(minute=0, second=0, microsecond=0))
    db.execute(
        delete(HourlyAggregate).where(
            HourlyAggregate.source_id == source.id,
            HourlyAggregate.period_start >= start,
            HourlyAggregate.period_start < end,
        )
    )
    db.execute(
        delete(DailySummary).where(
            DailySummary.source_id == source.id,
            DailySummary.period_start == start,
            DailySummary.method == VERSION,
        )
    )
    if cutoff <= start:
        return
    provider = db.get(Provider, source.provider_id)
    cadence = source.capabilities.get("native_cadence_seconds") or provider.capabilities.get(
        "native_cadence_seconds"
    )
    if not isinstance(cadence, (int, float)) or not 1 <= cadence <= 3600:
        return  # Unknown cadence cannot justify sample coverage.
    observations = db.scalars(
        select(Observation)
        .where(
            Observation.source_id == source.id,
            Observation.observed_at >= start - timedelta(days=1),
            Observation.observed_at <= end + timedelta(days=1),
        )
        .order_by(Observation.observed_at)
    ).all()
    for key, (description, samples) in channels(observations).items():
        if description["kind"] in {"daily_minimum", "daily_maximum", "rolling_total"}:
            continue
        stats = aggregate(samples, start, cutoff, cadence, description)
        # Preserve null/invalid windows too: coverage zero is meaningful.
        if (
            not any(start <= row.observed_at < end for row, _ in samples)
            and stats["sample_count"] == 0
        ):
            continue
        db.add(
            DailySummary(
                source_id=source.id,
                product=description["product"],
                period_start=start,
                period_end=cutoff,
                period_basis="Europe/Madrid",
                method=VERSION,
                channel=key,
                coverage=stats["coverage"],
                metrics={description["metric"]: stats},
                fetched_at=now,
                provisional=cutoff < end,
                provenance={
                    "calculation_version": VERSION,
                    "quality_policy": "invalid-excluded-v1",
                    "cutoff": cutoff.isoformat(),
                },
            )
        )
        hour = start
        while hour < cutoff:
            right = hour + timedelta(hours=1)
            stats = aggregate(samples, hour, right, cadence, description)
            db.add(
                HourlyAggregate(
                    source_id=source.id,
                    metric=description["metric"],
                    channel=key,
                    period_start=hour,
                    period_end=right,
                    minimum=stats["minimum"],
                    maximum=stats["maximum"],
                    mean=stats["mean"],
                    sample_count=stats["sample_count"],
                    coverage=stats["coverage"],
                    method=VERSION,
                    stats=stats,
                )
            )
            hour = right


def refresh_aggregates(engine, *, limit=8):
    """Station lock then dirty-row lock: same lock order as ingestion and exclusion."""
    count = 0
    for _ in range(limit):
        with Session(engine) as db, db.begin():
            candidate = db.execute(
                select(AggregateDirtyDay.source_id, AggregateDirtyDay.day)
                .order_by(AggregateDirtyDay.day, AggregateDirtyDay.source_id)
                .limit(1)
            ).first()
            if not candidate:
                break
            source = db.get(StationSource, candidate.source_id)
            if not db.scalar(
                select(Station.id)
                .where(Station.id == source.station_id)
                .with_for_update(skip_locked=True)
            ):
                break
            dirty = db.scalar(
                select(AggregateDirtyDay)
                .where(
                    AggregateDirtyDay.source_id == candidate.source_id,
                    AggregateDirtyDay.day == candidate.day,
                )
                .with_for_update(skip_locked=True)
            )
            if dirty is None:
                continue
            if db.scalar(eligible_source_ids().where(StationSource.id == source.id)):
                rebuild_day(db, source, dirty.day)
            db.delete(dirty)
            count += 1
    return count

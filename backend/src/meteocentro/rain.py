"""Rain totals derived at read time from the stored observations; nothing is persisted.

AEMET publishes 60-minute interval totals: the civil-day total is the sum of the
non-overlapping hours inside today (Europe/Madrid); a missing hour lowers coverage.

Meteoclimatic publishes a daily counter whose reset time is not documented and was
observed at UTC midnight, not Madrid midnight. Counter readings are never summed:
only increments between consecutive readings are. A rise adds the difference; a
single drop near midnight (Madrid or UTC) is a reset and adds the new value. Any
other descent makes the window unusable instead of guessing.
"""

from collections import defaultdict
from datetime import UTC, timedelta
from types import SimpleNamespace

from sqlalchemy import or_, select

from meteocentro.history import MADRID, civil_window, valid_value
from meteocentro.models import Observation

RAIN_METRICS = {"rain", "rain_today"}
DAY_BASIS = "Europe/Madrid_day_so_far"
COUNTER_BASIS = "counter_increments"
HOUR = timedelta(hours=1)
# A window starts at the reading nearest its nominal start, within this margin.
DAY_ANCHOR = timedelta(minutes=20)
HOUR_ANCHOR = timedelta(minutes=10)
# Longer steps are unobserved time: rain there is still counted if the counter
# rose, but coverage drops and the total is marked partial.
MAX_STEP = timedelta(minutes=30)


def plausible_reset(before, after):
    """Local 23:30–02:30 covers Madrid midnight and UTC midnight in CET and CEST."""
    for instant in (before, after):
        local = instant.astimezone(MADRID)
        minutes = local.hour * 60 + local.minute
        if minutes >= 23 * 60 + 30 or minutes <= 2 * 60 + 30:
            return True
    return False


def derived(metric, value, start, end, fetched_at, coverage, basis, derivation, notes=()):
    partial = coverage < 0.999 or bool(notes)
    return SimpleNamespace(
        metrics={
            metric: {
                "value": round(value, 2),
                "unit": "mm",
                "kind": "interval_total",
                "coverage": round(coverage, 4),
                "partial": partial,
                "derivation": derivation,
                "notes": sorted(notes),
            }
        },
        period_start=start,
        period_end=end,
        period_basis=basis,
        observed_at=end,
        fetched_at=fetched_at,
        product=derivation,
    )


def aemet_today(points, start, now):
    hours, last_end, notes = [], None, set()
    for point in sorted(points, key=lambda p: p.period_end or p.observed_at):
        value = valid_value(point.rain) if point.rain else None
        if (
            value is None
            or point.rain.get("kind") != "interval_total"
            or point.period_start is None
            or point.period_start < start
            or point.period_end > now
        ):
            continue
        if last_end and point.period_start < last_end:
            notes.add("overlapping_intervals")
            continue
        hours.append((point, value))
        last_end = point.period_end
    if not hours:
        return None
    covered = sum((p.period_end - p.period_start).total_seconds() for p, _ in hours)
    return derived(
        "rain_today",
        sum(value for _, value in hours),
        start,
        last_end,
        hours[-1][0].fetched_at,
        covered / (last_end - start).total_seconds(),
        DAY_BASIS,
        "aemet_hourly_sum",
        notes,
    )


def counter_increments(chain, target, anchor):
    near = [point for point in chain if abs(point[0] - target) <= anchor]
    if not near:
        return None
    base = min(near, key=lambda point: (abs(point[0] - target), point[0]))
    steps = [point for point in chain if point[0] >= base[0]]
    if len(steps) < 2:
        return None
    total, covered, notes = 0.0, 0.0, set()
    for (before, old, _), (after, new, _) in zip(steps, steps[1:], strict=False):
        if new >= old:
            total += new - old
        elif "counter_reset" not in notes and plausible_reset(before, after):
            # Rain between the last reading and the reset itself is not observable.
            total += new
            notes.add("counter_reset")
        else:
            return None
        if after - before <= MAX_STEP:
            covered += (after - before).total_seconds()
        else:
            notes.add("reading_gap")
    span = (steps[-1][0] - base[0]).total_seconds()
    return total, base[0], steps[-1], covered / span, notes


def counter_readings(points, start, metrics):
    chain = [
        (point.observed_at, value, point)
        for point in points
        if point.counter and (value := valid_value(point.counter)) is not None
    ]
    if not chain:
        return []
    last = chain[-1][0]
    result = []
    for metric, target, anchor, basis in (
        ("rain_today", start, DAY_ANCHOR, DAY_BASIS),
        ("rain", last - HOUR, HOUR_ANCHOR, COUNTER_BASIS),
    ):
        if metric not in metrics or (metric == "rain_today" and last < start):
            continue
        found = counter_increments(chain, target, anchor)
        if found is None:
            continue
        total, first, (end, _, point), coverage, notes = found
        result.append(
            (
                metric,
                derived(
                    metric,
                    total,
                    first,
                    end,
                    point.fetched_at,
                    coverage,
                    basis,
                    "meteoclimatic_counter_increments",
                    notes - {"counter_reset"},
                ),
            )
        )
    return result


def derived_rows(db, rows, now, metrics):
    """Extra projection rows (station, source, provider, metric, observation).

    Only origins already present in the eligible projection are read, so exclusions
    apply unchanged. One statement for the whole population.
    """
    metrics = set(metrics) & RAIN_METRICS
    origins = {
        source.id: (station, source, provider)
        for station, source, provider, *_ in rows
        if provider.code in {"aemet", "meteoclimatic"}
    }
    if not metrics or not origins:
        return []
    now = now.astimezone(UTC)
    start, _ = civil_window(now.astimezone(MADRID).date())
    since = min(start - DAY_ANCHOR, now - HOUR - HOUR_ANCHOR)
    samples = defaultdict(list)
    for source_id, observed, fetched, period_start, period_end, rain, counter in db.execute(
        select(
            Observation.source_id,
            Observation.observed_at,
            Observation.fetched_at,
            Observation.period_start,
            Observation.period_end,
            Observation.metrics["rain"],
            Observation.metrics["rain_daily"],
        )
        .where(
            Observation.source_id.in_(list(origins)),
            Observation.observed_at >= since,
            Observation.observed_at <= now,
            or_(Observation.metrics.has_key("rain"), Observation.metrics.has_key("rain_daily")),
        )
        .order_by(Observation.observed_at, Observation.id)
    ):
        # psycopg may return Europe/Madrid datetimes; interval arithmetic is UTC only.
        samples[source_id].append(
            SimpleNamespace(
                observed_at=observed.astimezone(UTC),
                fetched_at=fetched,
                period_start=period_start.astimezone(UTC) if period_start else None,
                period_end=period_end.astimezone(UTC) if period_end else None,
                rain=rain,
                counter=counter,
            )
        )
    result = []
    for source_id, points in samples.items():
        station, source, provider = origins[source_id]
        if provider.code == "aemet":
            today = aemet_today(points, start, now) if "rain_today" in metrics else None
            readings = [("rain_today", today)] if today else []
        else:
            readings = counter_readings(points, start, metrics)
        result += [(station, source, provider, name, obs) for name, obs in readings]
    return result

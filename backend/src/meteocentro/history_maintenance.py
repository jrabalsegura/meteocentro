"""Local maintenance and non-destructive retention inspection; no remote service actions."""

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from meteocentro.config import get_settings
from meteocentro.db import get_engine
from meteocentro.history import MADRID, VERSION, refresh_aggregates
from meteocentro.history_import import enqueue_history
from meteocentro.models import Job


def month_after(day, count=1):
    ordinal = day.year * 12 + day.month - 1 + count
    return date(ordinal // 12, ordinal % 12 + 1, 1)


def ensure_partitions(db, now):
    for i in range(4):
        day = month_after(now.date(), i)
        end = month_after(day)
        name = f"observations_{day:%Y%m}"
        if db.scalar(text("SELECT to_regclass(:name)"), {"name": name}):
            continue
        # Never delete/move default rows behind the back of dependent FKs.
        if db.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM observations_default "
                "WHERE observed_at>=:a AND observed_at<:b)"
            ),
            {
                "a": datetime.combine(day, datetime.min.time(), UTC),
                "b": datetime.combine(end, datetime.min.time(), UTC),
            },
        ):
            continue
        db.execute(
            text(
                f"CREATE TABLE {name} PARTITION OF observations "
                f"FOR VALUES FROM ('{day} 00:00:00+00') TO ('{end} 00:00:00+00')"
            )
        )


def schedule_maintenance(engine):
    with Session(engine) as db, db.begin():
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(746302005)")):
            return
        now = db.scalar(select(func.clock_timestamp())).astimezone(UTC)
        job = db.scalar(
            select(Job).where(Job.dedupe_key == "local:aggregate-refresh").with_for_update()
        )
        if job and job.next_run_at > now:
            return
        if job is None:
            job = Job(
                kind="aggregate_refresh",
                status="local",
                dedupe_key="local:aggregate-refresh",
                next_run_at=now,
            )
            db.add(job)
        ensure_partitions(db, now)
        # Hourly current-day refresh, nightly review of the last two days.
        days = [now.astimezone(MADRID).date()]
        if not job.cursor or job.cursor.get("review_day") != str(days[0]):
            days.extend([days[0] - timedelta(days=1), days[0] - timedelta(days=2)])
        # No station locks here: this transaction only schedules work.
        for day in days:
            db.execute(
                text("""INSERT INTO aggregate_dirty_days(source_id,day)
                SELECT id,:day FROM station_sources WHERE status='enabled'
                ON CONFLICT (source_id,day) DO NOTHING"""),
                {"day": day},
            )
        job.cursor = {"review_day": str(days[0])}
        job.next_run_at = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def retention_preview(db, months, now=None):
    now = now or datetime.now(UTC)
    cutoff = datetime.combine(month_after(now.date(), -months), datetime.min.time(), UTC)
    # Require the current calculation version and no dirty day before even proposing removal.
    rows = (
        db.execute(
            text("""SELECT p.code, count(*) AS candidate_rows,
        count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM daily_summaries d
          WHERE d.source_id=o.source_id AND d.method=:version
          AND d.period_start <= o.observed_at AND d.period_end > o.observed_at)
          OR EXISTS(SELECT 1 FROM aggregate_dirty_days x WHERE x.source_id=o.source_id
          AND x.day=(o.observed_at AT TIME ZONE 'Europe/Madrid')::date)) AS unverified_rows,
        sum(pg_column_size(o)) AS row_bytes
        FROM observations o JOIN station_sources s ON s.id=o.source_id
        JOIN providers p ON p.id=s.provider_id
        WHERE o.observed_at<:cutoff GROUP BY p.code"""),
            {"cutoff": cutoff, "version": VERSION},
        )
        .mappings()
        .all()
    )
    return {
        "dry_run": True,
        "purge_enabled": False,
        "cutoff": cutoff,
        "months": months,
        "providers": [{**r, "permission_review_required": r["code"] != "aemet"} for r in rows],
        "notice": (
            "No se borra nada. La cobertura y cada métrica requieren verificación "
            "antes de habilitar una purga."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=UUID)
    parser.add_argument("--from", dest="start", type=date.fromisoformat)
    parser.add_argument("--to", dest="end", type=date.fromisoformat)
    parser.add_argument(
        "--refresh-job",
        type=UUID,
        help="Explicitly recheck a completed daily window for provider corrections",
    )
    parser.add_argument("--retention-preview", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    engine = get_engine()
    if args.rebuild:
        schedule_maintenance(engine)
        print(json.dumps({"days_rebuilt": refresh_aggregates(engine, limit=10000)}))
        return
    with Session(engine) as db, db.begin():
        if args.retention_preview:
            print(
                json.dumps(
                    retention_preview(db, get_settings().detail_retention_months), default=str
                )
            )
        elif args.refresh_job:
            job = db.get(Job, args.refresh_job, with_for_update=True)
            if not job or job.kind != "history" or job.status != "completed":
                parser.error("Only completed history jobs can be refreshed")
            job.cursor = {k: job.cursor[k] for k in ("from", "to")}
            job.status = "pending"
            job.next_run_at = datetime.now(UTC)
            print(json.dumps({"queued": str(job.id)}))
        elif args.source and args.start and args.end:
            try:
                print(
                    json.dumps({"queued": enqueue_history(db, args.source, args.start, args.end)})
                )
            except ValueError as error:
                parser.error(str(error))
        else:
            parser.error(
                "Use --source/--from/--to, --refresh-job, --rebuild or --retention-preview"
            )


if __name__ == "__main__":
    main()

"""Bounded AEMET daily importer. Local collection is a different product."""

import re
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from meteocentro.aemet import BASE, Batch, checked_url, decimal_value, digest, folded
from meteocentro.domain.eligibility import eligible_source_ids
from meteocentro.ingestion_errors import IngestionError
from meteocentro.job_queue import db_now
from meteocentro.models import (
    DailySummary,
    DailySummaryRevision,
    Job,
    Provider,
    Station,
    StationSource,
)


def validate_daily_metadata(fields):
    if not isinstance(fields, list) or any(
        not isinstance(f, dict) or not isinstance(f.get("id"), str) for f in fields
    ):
        raise IngestionError("daily_metadata_changed", pause=True)
    by_id = {f["id"]: folded(str(f)) for f in fields}
    if len(by_id) != len(fields):
        raise IngestionError("daily_metadata_changed", pause=True)
    checks = {
        "tmin": "c",
        "tmax": "c",
        "tmed": "c",
        "prec": "07 a 07",
        "horatmin": "utc",
        "horatmax": "utc",
        "racha": "m/s",
        "velmedia": "m/s",
        "presmax": "estacion",
        "presmin": "estacion",
    }
    if not {"fecha", "indicativo"} <= by_id.keys() or any(
        key not in by_id or text not in by_id[key] for key, text in checks.items()
    ):
        raise IngestionError("daily_metadata_changed", pause=True)


def download_daily(adapter, external_id, start, end):
    if not adapter.key:
        raise IngestionError("pending_access", pause=True)
    if not re.fullmatch(r"[A-Za-z0-9]{1,20}", external_id) or not 0 < (end - start).days <= 30:
        raise IngestionError("invalid_history_window")
    path = (
        f"valores/climatologicos/diarios/datos/fechaini/{start.isoformat()}T00:00:00UTC/"
        f"fechafin/{(end - timedelta(days=1)).isoformat()}T23:59:59UTC/estacion/{external_id}"
    )
    envelope = adapter.request(BASE + path, authenticated=True)
    data_url, metadata_url = (checked_url(envelope.get(k)) for k in ("datos", "metadatos"))
    fields = adapter.get_metadata("daily")
    if fields is None:
        metadata = adapter.request(metadata_url)
        fields = metadata.get("campos") if isinstance(metadata, dict) else None
        validate_daily_metadata(fields)
        adapter.save_metadata("daily", fields)
    validate_daily_metadata(fields)
    records = adapter.request(data_url)
    if not isinstance(records, list):
        raise IngestionError("invalid_daily_records")
    return Batch(records, fields, digest(fields), datetime.now(UTC))


def numeric(row, field, low, high):
    raw = row.get(field)
    flags = []
    if raw in (None, "", "Ip", "Acum"):
        return None, (
            ["trace_below_0.1"]
            if raw == "Ip"
            else ["accumulated_unknown_period"]
            if raw == "Acum"
            else ["missing"]
        )
    try:
        value = decimal_value(raw)
        if value is None or not low <= value <= high:
            flags = ["outside_physical_range"]
            value = None
    except ValueError:
        value = None
        flags = ["invalid"]
    return float(value) if value is not None else None, flags


def normalize_daily(row, batch, external_id, start, end):
    if row.get("indicativo") != external_id:
        raise IngestionError("daily_identity_mismatch", pause=True)
    try:
        day = date.fromisoformat(row["fecha"])
    except (ValueError, KeyError, TypeError):
        raise IngestionError("invalid_daily_date", pause=True) from None
    if not start <= day < end:
        raise IngestionError("daily_outside_window", pause=True)
    # The provider convention remains explicit, never relabelled Europe/Madrid.
    specs = [
        (
            "temperature",
            "°C",
            [("minimum", "tmin", -90, 65), ("maximum", "tmax", -90, 65), ("mean", "tmed", -90, 65)],
        ),
        ("rain", "mm", [("total", "prec", 0, 2000)]),
        ("wind_speed", "m/s", [("mean", "velmedia", 0, 150)]),
        ("wind_gust", "m/s", [("maximum", "racha", 0, 150)]),
        (
            "pressure_station",
            "hPa",
            [("minimum", "presmin", 100, 1100), ("maximum", "presmax", 100, 1100)],
        ),
        (
            "humidity",
            "%",
            [
                ("minimum", "hrmin", 0, 100),
                ("maximum", "hrmax", 0, 100),
                ("mean", "hrmedia", 0, 100),
            ],
        ),
    ]
    summaries = []
    for metric, unit, fields in specs:
        a = datetime.combine(day, datetime.min.time(), UTC) + timedelta(
            hours=7 if metric == "rain" else 0
        )
        basis = "AEMET_07_07_UTC" if metric == "rain" else "AEMET_provider_date"
        stats = {
            "metric": metric,
            "unit": unit,
            "minimum": None,
            "maximum": None,
            "mean": None,
            "total": None,
            "coverage": None,
            "partial": True,
            "covered_seconds": None,
            "flags": ["provider_coverage_unknown"],
            "aggregation_method": "provider",
            "mean_method": "AEMET_reported",
            "original": {},
        }
        for statistic, field, low, high in fields:
            value, flags = numeric(row, field, low, high)
            stats[statistic] = value
            stats["original"][field] = row.get(field)
            stats["flags"].extend(flags)
            hour = row.get("hora" + field)
            if hour is not None:
                stats["original"]["hora" + field] = hour
            if (
                statistic in {"minimum", "maximum"}
                and isinstance(hour, str)
                and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", hour)
            ):
                stats[statistic + "_reported_hour_UTC"] = hour
        summaries.append(
            dict(
                product="aemet_daily",
                period_start=a,
                period_end=a + timedelta(days=1),
                period_basis=basis,
                method="provider",
                channel=digest({"product": "aemet_daily", "metric": metric, "basis": basis}),
                metrics={metric: stats},
                coverage=None,
                fetched_at=batch.fetched_at,
                provisional=False,
                provenance={
                    "payload_hash": digest(row),
                    "metadata_hash": batch.metadata_hash,
                    "normalizer_version": "aemet-daily-v1",
                    "provider_date": day.isoformat(),
                    "window_boundaries_verified": metric == "rain",
                    "raw": row,
                },
            )
        )
    return summaries


def enqueue_history(db, source_id, start, end):
    from meteocentro.administration import admin_lock

    admin_lock(db)
    if not 0 < (end - start).days <= 30 or end > datetime.now(UTC).date():
        raise ValueError("pilot_requires_1_to_30_past_days")
    source = db.get(StationSource, source_id)
    if source is None or not db.scalar(eligible_source_ids().where(StationSource.id == source_id)):
        raise ValueError("source_not_eligible")
    provider = db.get(Provider, source.provider_id)
    if provider.code != "aemet" or not source.capabilities.get("daily_history"):
        raise ValueError("daily_import_pending_access_or_terms")
    # Source lock serializes overlapping enqueue requests, including completed windows.
    db.execute(select(Station.id).where(Station.id == source.station_id).with_for_update())
    if not db.scalar(eligible_source_ids().where(StationSource.id == source_id)):
        raise ValueError("source_not_eligible")
    jobs = db.scalars(select(Job).where(Job.source_id == source_id, Job.kind == "history")).all()
    occupied = set()
    for job in jobs:
        left = date.fromisoformat(job.cursor["from"])
        right = date.fromisoformat(job.cursor["to"])
        occupied.update(left + timedelta(days=i) for i in range((right - left).days))
    count = 0
    while start < end:
        if start in occupied:
            start += timedelta(days=1)
            continue
        right = start + timedelta(days=1)
        while right < end and right not in occupied:
            right += timedelta(days=1)
        result = db.execute(
            insert(Job)
            .values(
                kind="history",
                provider_id=provider.id,
                source_id=source_id,
                status="pending",
                next_run_at=db_now(db),
                priority=1,
                interval_seconds=86400,
                dedupe_key=f"history:{source_id}:{start}:{right}",
                cursor={"from": start.isoformat(), "to": right.isoformat()},
            )
            .on_conflict_do_nothing(index_elements=[Job.dedupe_key])
            .returning(Job.id)
        )
        count += int(result.scalar_one_or_none() is not None)
        start = right
    return count


def run_history(queue, claim, adapter, *, after_chunk=None):
    if claim.cursor.get("confirmed"):
        return {"complete": True, "resumed_after_commit": True}, claim.cursor
    with Session(queue.engine) as db:
        job = db.get(Job, claim.job_id)
        source = db.get(StationSource, job.source_id)
        external_id = source.external_id
        if not db.scalar(eligible_source_ids().where(StationSource.id == source.id)):
            return {"excluded": 1, "complete": True}, claim.cursor
    start, end = (date.fromisoformat(claim.cursor[k]) for k in ("from", "to"))
    # One bounded window per claim. Cursor and summaries commit atomically before acknowledgement.
    try:
        batch = download_daily(adapter, external_id, start, end)
    except IngestionError as error:
        if error.code != "product_unavailable":
            raise
        return {"complete": True, "availability": "unavailable"}, {
            **claim.cursor,
            "availability": "unavailable",
        }
    prepared = [
        item
        for row in batch.records
        for item in normalize_daily(row, batch, external_id, start, end)
    ]
    identities = {}
    for item in prepared:
        key = (item["channel"], item["period_start"])
        if key in identities and identities[key] != item:
            raise IngestionError("contradictory_daily_rows", pause=True)
        identities[key] = item
    inserted = revised = unchanged = 0
    with Session(queue.engine) as db, db.begin():
        job = queue.fence(db, claim)
        source = db.get(StationSource, job.source_id)
        db.execute(select(Station.id).where(Station.id == source.station_id).with_for_update())
        if not db.scalar(eligible_source_ids().where(StationSource.id == source.id)):
            return {"excluded": 1, "complete": True}, claim.cursor
        for item in identities.values():
            row = db.scalar(
                select(DailySummary).where(
                    DailySummary.source_id == source.id,
                    DailySummary.channel == item["channel"],
                    DailySummary.period_start == item["period_start"],
                    DailySummary.method == "provider",
                )
            )
            if row is None:
                db.add(DailySummary(source_id=source.id, **item))
                inserted += 1
            elif row.metrics != item["metrics"] or row.provenance != item["provenance"]:
                db.add(
                    DailySummaryRevision(
                        summary_id=row.id,
                        previous={
                            "metrics": row.metrics,
                            "provenance": row.provenance,
                            "fetched_at": row.fetched_at.isoformat(),
                        },
                    )
                )
                for key, value in item.items():
                    setattr(row, key, value)
                revised += 1
            else:
                unchanged += 1
        job.cursor = {
            **claim.cursor,
            "confirmed": True,
            "rows": len(batch.records),
            "fetched_at": batch.fetched_at.isoformat(),
        }
        queue.fence(db, claim)
        cursor = job.cursor
    if after_chunk:
        after_chunk()
    return {
        "inserted": inserted,
        "revised": revised,
        "unchanged": unchanged,
        "received": len(batch.records),
        "complete": True,
    }, cursor

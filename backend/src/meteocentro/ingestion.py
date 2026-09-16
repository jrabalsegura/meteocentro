"""Short, fenced transactions. A committed batch is safe to replay after any crash."""

from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from meteocentro.aemet import (
    Batch,
    digest,
    measurement,
    normalize,
    observation_time,
    station_record,
)
from meteocentro.catalog import review_absences
from meteocentro.domain.eligibility import eligible_source_ids
from meteocentro.domain.observations import MetricKind
from meteocentro.domain.provinces import classify_province
from meteocentro.job_queue import Claim, Queue, db_now
from meteocentro.models import (
    IngestionRun,
    LatestObservation,
    Observation,
    ObservationRevision,
    ProductMetadata,
)


class Ingestor:
    def __init__(self, queue: Queue, claim: Claim):
        self.queue, self.claim = queue, claim

    def get_metadata(self, product: str):
        with Session(self.queue.engine) as db:
            snapshot = db.scalar(
                select(ProductMetadata)
                .where(
                    ProductMetadata.provider_id == self.claim.provider_id,
                    ProductMetadata.product == product,
                    ProductMetadata.fetched_at > db_now(db) - timedelta(days=1),
                )
                .order_by(ProductMetadata.fetched_at.desc())
                .limit(1)
            )
            return snapshot.fields if snapshot else None

    def save_metadata(self, product: str, fields: list):
        with Session(self.queue.engine) as db, db.begin():
            self.queue.fence(db, self.claim)
            db.execute(
                insert(ProductMetadata)
                .values(
                    provider_id=self.claim.provider_id,
                    product=product,
                    fields=fields,
                    payload_hash=digest(fields),
                    fetched_at=db_now(db),
                )
                .on_conflict_do_update(
                    constraint="uq_product_metadata", set_={"fetched_at": db_now(db)}
                )
            )

    def catalog(self, db: Session, info: dict, province: str | None, counters: Counter):
        from meteocentro.catalog import upsert_source
        from meteocentro.models import Provider

        return upsert_source(
            db,
            db.get(Provider, self.claim.provider_id),
            info,
            counters,
            capability="current" if self.claim.kind == "current" else "daily_history",
        )

    def store_observation(self, db, item, source, quality, counters):
        metrics = {
            name: metric.model_dump(
                mode="json", exclude={"period_basis"} if metric.period_basis is None else set()
            )
            for name, metric in item.metrics.items()
        }
        observation = db.scalar(
            select(Observation).where(
                Observation.source_id == source.id,
                Observation.product == item.product,
                Observation.observed_at == item.observed_at,
                Observation.period_start.is_not_distinct_from(item.period_start),
                Observation.period_end.is_not_distinct_from(item.period_end),
                Observation.period_basis.is_not_distinct_from(item.period_basis),
            )
        )
        if observation is None:
            observation = Observation(
                **item.model_dump(exclude={"metrics", "source_id"}),
                source_id=source.id,
                metrics=metrics,
                quality=quality,
            )
            db.add(observation)
            db.flush()
            counters["inserted"] += 1
        elif (
            observation.payload_hash != item.payload_hash
            or observation.metrics != metrics
            or observation.quality != quality
            or observation.normalizer_version != item.normalizer_version
        ):
            db.add(
                ObservationRevision(
                    observation_id=observation.id,
                    previous_metrics=observation.metrics,
                    previous_quality=observation.quality,
                    previous_payload_hash=observation.payload_hash,
                )
            )
            observation.metrics, observation.quality = metrics, quality
            observation.payload_hash = item.payload_hash
            observation.normalizer_version = item.normalizer_version
            observation.fetched_at, observation.revised_at = item.fetched_at, db_now(db)
            counters["revised"] += 1
        else:
            counters["unchanged"] += 1
        db.flush()
        for name, metric in item.metrics.items():
            if metric.value is not None:
                statement = insert(LatestObservation).values(
                    source_id=source.id,
                    metric=name,
                    observation_id=observation.id,
                    observed_at=item.observed_at,
                )
                db.execute(
                    statement.on_conflict_do_update(
                        constraint="uq_latest_source_metric",
                        set_={"observation_id": observation.id, "observed_at": item.observed_at},
                        where=LatestObservation.observed_at <= item.observed_at,
                    )
                )
            else:
                # A correction to null must not leave a latest pointer to an unusable value.
                removed = db.execute(
                    delete(LatestObservation).where(
                        LatestObservation.source_id == source.id,
                        LatestObservation.metric == name,
                        LatestObservation.observation_id == observation.id,
                    )
                ).rowcount
                if removed:
                    previous = db.scalar(
                        select(Observation)
                        .where(
                            Observation.source_id == source.id,
                            Observation.metrics[name]["value"].as_string().is_not(None),
                        )
                        .order_by(Observation.observed_at.desc())
                        .limit(1)
                    )
                    if previous:
                        db.add(
                            LatestObservation(
                                source_id=source.id,
                                metric=name,
                                observation_id=previous.id,
                                observed_at=previous.observed_at,
                            )
                        )

    def ingest(self, batch: Batch, *, after_chunk=None) -> tuple[dict, dict]:
        counters = Counter(
            {
                name: 0
                for name in (
                    "valid",
                    "outside",
                    "excluded",
                    "invalid",
                    "inserted",
                    "revised",
                    "unchanged",
                    "new_sources",
                    "location_review",
                    "updated_sources",
                    "potential_duplicates",
                    "pending",
                    "absent_sources",
                    "absence_review",
                )
            }
        )
        watermarks = dict(self.claim.cursor.get("sources", {}))
        available = defaultdict(list)
        prepared = []
        for row in batch.records:
            try:
                info = station_record(row, self.claim.kind)
                province = classify_province(float(info["longitude"]), float(info["latitude"]))
                if province is None:
                    counters["outside"] += 1
                    continue
                normalized = normalize(row, batch) if self.claim.kind == "current" else []
                if normalized:
                    available[info["external_id"]].append(observation_time(row))
                prepared.append((info, province, normalized, row))
            except (ValueError, TypeError, KeyError, OverflowError):
                counters["invalid"] += 1
        # Deterministic station lock ordering reduces cross-worker deadlocks.
        prepared.sort(key=lambda item: (item[0]["external_id"], str(item[3].get("fint", ""))))
        newest = None
        for start in range(0, len(prepared), 25):
            with Session(self.queue.engine) as db, db.begin():
                self.queue.fence(db, self.claim)
                for info, province, normalized, row in prepared[start : start + 25]:
                    source = self.catalog(db, info, province, counters)
                    if source is None:
                        continue
                    if source.id not in db.scalars(eligible_source_ids(source.station_id)).all():
                        counters["excluded"] += 1
                        continue
                    quality = {"provider": "AEMET", "metadata_hash": batch.metadata_hash}
                    if normalized:
                        sensors = {
                            field: measurement(
                                row, field, "mm", MetricKind.INTERVAL_TOTAL, 0, 2000
                            ).model_dump(mode="json")
                            for field in ("prec", "pacutp")
                        }
                        quality["rain_sensors"] = sensors
                        quality["rain_sensor"] = (
                            "prec"
                            if sensors["prec"]["value"] is not None
                            else "pacutp"
                            if sensors["pacutp"]["value"] is not None
                            else None
                        )
                    for item in normalized:
                        self.store_observation(db, item, source, quality, counters)
                        stamp = item.observed_at.isoformat()
                        watermarks[info["external_id"]] = max(
                            stamp, watermarks.get(info["external_id"], stamp)
                        )
                        newest = max(newest or stamp, stamp)
                    counters["valid"] += 1
                # Do not commit data after expiration, even if no other worker reclaimed yet.
                self.queue.fence(db, self.claim)
                db.get(IngestionRun, self.claim.run_id).result = dict(counters)
            if after_chunk:
                after_chunk()
        gaps = []
        if self.claim.kind == "current":
            from datetime import datetime

            with Session(self.queue.engine) as db, db.begin():
                self.queue.fence(db, self.claim)
                review_absences(
                    db,
                    self.claim.provider_id,
                    {row.get("idema") for row in batch.records if isinstance(row, dict)},
                    batch.fetched_at,
                    counters,
                )
                self.queue.fence(db, self.claim)

            for identity, watermark in self.claim.cursor.get("sources", {}).items():
                times = sorted(set(available.get(identity, [])))
                previous = datetime.fromisoformat(watermark)
                if times and times[0] - previous > timedelta(hours=1):
                    gaps.append(
                        {
                            "external_id": identity,
                            "after": watermark,
                            "before": times[0].isoformat(),
                            "reason": "outside_available_window",
                        }
                    )
                elif not times:
                    gaps.append(
                        {
                            "external_id": identity,
                            "after": watermark,
                            "before": batch.fetched_at.isoformat(),
                            "reason": "source_absent_in_batch",
                        }
                    )
        result = {
            **counters,
            "received": len(batch.records),
            "gaps": gaps,
            "newest_observed_at": newest,
            "metadata_hash": batch.metadata_hash,
            "new_data": counters["inserted"] > 0,
            "lag_seconds": None,
        }
        if newest:
            from datetime import datetime

            result["lag_seconds"] = max(
                0, (batch.fetched_at - datetime.fromisoformat(newest)).total_seconds()
            )
        return result, {"sources": watermarks, "last_confirmed_at": batch.fetched_at.isoformat()}

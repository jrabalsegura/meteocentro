from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MetricKind(StrEnum):
    INSTANT = "instant"
    INTERVAL_MEAN = "interval_mean"
    INTERVAL_TOTAL = "interval_total"
    DAILY_COUNTER = "daily_counter"
    DAILY_MINIMUM = "daily_minimum"
    DAILY_MAXIMUM = "daily_maximum"
    ROLLING_TOTAL = "rolling_total"
    RATE = "rate"
    STATION_PRESSURE = "station_pressure"
    SEA_LEVEL_PRESSURE = "sea_level_pressure"


class Measurement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Decimal | None = Field(allow_inf_nan=False)
    unit: str
    kind: MetricKind
    original_value: Decimal | None = Field(default=None, allow_inf_nan=False)
    original_unit: str | None = None
    provider_quality: str | None = None
    plausibility_flags: list[str] = Field(default_factory=list)
    period_basis: str | None = None


class NormalizedObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: UUID
    product: str = Field(min_length=1, max_length=100)
    observed_at: datetime
    fetched_at: datetime
    period_start: datetime | None = None
    period_end: datetime | None = None
    period_basis: str | None = None
    metrics: dict[str, Measurement]
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalizer_version: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_semantics(self):
        for name in ("observed_at", "fetched_at", "period_start", "period_end"):
            instant = getattr(self, name)
            if instant is not None:
                if instant.utcoffset() is None:
                    raise ValueError(f"{name} must include a timezone")
                setattr(self, name, instant.astimezone(UTC))
        if (self.period_start is None) != (self.period_end is None):
            raise ValueError("period_start and period_end must both be set or both absent")
        if self.period_start and self.period_end and self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")
        if self.period_start and not self.period_basis:
            raise ValueError("period_basis required for a measured period")
        if self.period_start is None and self.period_basis is not None:
            raise ValueError("period_basis requires a measured period")
        for name, metric in self.metrics.items():
            if (name == "rain" or name.startswith("rain_")) and metric.kind not in {
                MetricKind.INTERVAL_TOTAL,
                MetricKind.DAILY_COUNTER,
                MetricKind.ROLLING_TOTAL,
                MetricKind.RATE,
            }:
                raise ValueError(f"rain metric {name} lacks rain semantics")
            if name == "pressure_station" and metric.kind != MetricKind.STATION_PRESSURE:
                raise ValueError("station pressure has wrong kind")
            if name == "pressure_sea_level" and metric.kind != MetricKind.SEA_LEVEL_PRESSURE:
                raise ValueError("sea-level pressure has wrong kind")
        return self


def convert(value: Decimal | None, source_unit: str, target_unit: str) -> Decimal | None:
    if value is None:
        return None
    if source_unit == target_unit:
        return value
    conversions = {
        ("km/h", "m/s"): lambda x: x / Decimal("3.6"),
        ("m/s", "km/h"): lambda x: x * Decimal("3.6"),
        ("Pa", "hPa"): lambda x: x / Decimal("100"),
        ("°F", "°C"): lambda x: (x - Decimal("32")) * Decimal("5") / Decimal("9"),
    }
    try:
        return conversions[(source_unit, target_unit)](value)
    except KeyError as error:
        raise ValueError(f"unsupported conversion: {source_unit} to {target_unit}") from error


def normalized_measurement(
    value: Decimal | None,
    source_unit: str,
    canonical_unit: str,
    kind: MetricKind,
    provider_quality: str | None = None,
) -> Measurement:
    return Measurement(
        value=convert(value, source_unit, canonical_unit),
        unit=canonical_unit,
        kind=kind,
        original_value=value,
        original_unit=source_unit,
        provider_quality=provider_quality,
    )


def hash_payload(payload: bytes) -> str:
    return sha256(payload).hexdigest()

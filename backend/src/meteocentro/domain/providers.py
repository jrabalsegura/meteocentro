from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from meteocentro.domain.observations import NormalizedObservation


class ResultStatus(StrEnum):
    OK = "ok"
    UNSUPPORTED = "unsupported"
    PENDING_ACCESS = "pending_access"
    PENDING_TERMS = "pending_terms"
    ERROR = "error"


@dataclass(frozen=True)
class ProviderResult[T]:
    status: ResultStatus
    data: T | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Capabilities:
    discover: bool = False
    current: bool = False
    history: bool = False
    daily_history: bool = False


class ProviderAdapter(ABC):
    @property
    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    def discover(self) -> ProviderResult[list[dict]]:
        return ProviderResult(status=ResultStatus.UNSUPPORTED, reason="discovery not supported")

    def fetch_current(self) -> ProviderResult[list[NormalizedObservation]]:
        return ProviderResult(status=ResultStatus.UNSUPPORTED, reason="current not supported")

    def fetch_daily_history(
        self, external_id: str, start: date, end: date
    ) -> ProviderResult[list[dict]]:
        return ProviderResult(status=ResultStatus.UNSUPPORTED, reason="daily history not supported")

    def fetch_history(
        self, external_id: str, start: datetime, end: datetime
    ) -> ProviderResult[list[NormalizedObservation]]:
        return ProviderResult(status=ResultStatus.UNSUPPORTED, reason="history not supported")

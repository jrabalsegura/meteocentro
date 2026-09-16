from datetime import datetime


class IngestionError(Exception):
    """Only a fixed code may cross the logging/persistence boundary."""

    def __init__(self, code: str, *, retry_at: datetime | None = None, pause: bool = False):
        super().__init__(code)
        self.code = code
        self.retry_at = retry_at
        self.pause = pause


class LeaseLost(Exception):
    pass

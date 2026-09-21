"""Bounded database/schema wait; migrations are an explicit release operation."""

import os
import subprocess
import sys
import time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from meteocentro.config import get_settings
from meteocentro.db import get_engine
from meteocentro.schema import EXPECTED_REVISION


def main():
    role = sys.argv[1]
    if role not in {"api", "worker", "migrate"}:
        raise SystemExit("Unknown role")
    try:
        get_settings()
    except ValueError:
        raise SystemExit("invalid_configuration") from None
    for _ in range(30):
        try:
            with get_engine().connect() as db:
                if role == "migrate":
                    db.execute(text("SELECT 1"))
                    break
                if (
                    db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    != EXPECTED_REVISION
                ):
                    raise SystemExit("schema_mismatch: run the release migration first")
                break
        except SQLAlchemyError:
            time.sleep(2)
    else:
        raise SystemExit("database_unavailable")
    commands = {
        "api": [
            "uvicorn",
            "meteocentro.api:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--proxy-headers",
            "--forwarded-allow-ips=*",
            "--no-access-log",
        ],
        "worker": ["python", "-m", "meteocentro.worker"],
        "migrate": ["alembic", "upgrade", "head"],
    }
    if role == "migrate":
        # Serialize deployers even if they accidentally use different state directories.
        with get_engine().connect() as db:
            if not db.scalar(text("SELECT pg_try_advisory_lock(746307001)")):
                raise SystemExit("migration_already_running")
            try:
                result = subprocess.run(commands[role], check=False)
            finally:
                db.execute(text("SELECT pg_advisory_unlock(746307001)"))
        raise SystemExit(result.returncode)
    get_engine().dispose()
    os.execvp(commands[role][0], commands[role])


if __name__ == "__main__":
    main()

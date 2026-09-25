import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from meteocentro.api import app
from meteocentro.config import get_settings
from meteocentro.db import get_engine, session_factory
from meteocentro.models import Base
from meteocentro.schema import EXPECTED_REVISION
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def engine():
    url = os.getenv("TEST_DATABASE_URL")
    assert url, "TEST_DATABASE_URL must point to a dedicated PostgreSQL test database"
    assert make_url(url).database.endswith("_test"), (
        "refusing to alter a non-test database"
    )
    os.environ["DATABASE_URL"] = url
    # Existing public contracts are tested explicitly in public mode.
    # Phase 6 separately covers the default private mode on every route.
    os.environ["PRIVATE_READ"] = "false"
    get_settings.cache_clear()
    get_engine.cache_clear()
    session_factory.cache_clear()
    config = Config(str(ROOT / "backend/alembic.ini"))
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == EXPECTED_REVISION
        )
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine):
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {tables} CASCADE"))
    with Session(engine, expire_on_commit=False) as session:
        yield session
        session.rollback()
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {tables} CASCADE"))
    app.dependency_overrides.clear()

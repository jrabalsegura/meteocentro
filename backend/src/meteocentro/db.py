from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from meteocentro.config import get_settings


@lru_cache
def get_engine():
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 10,
            "options": "-c timezone=UTC -c statement_timeout=30000 -c lock_timeout=10000",
        },
    )


@lru_cache
def session_factory():
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with session_factory()() as session:
        yield session

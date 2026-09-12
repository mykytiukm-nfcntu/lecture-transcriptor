"""SQLAlchemy engine, session factory, declarative base, and startup helpers."""
from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.engine import Dialect, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model in `app.models`."""


class UtcDateTime(TypeDecorator[datetime]):
    """`DateTime(timezone=True)` that guarantees UTC-aware values on read.

    SQLite drops tzinfo on load, which mixes naive and aware datetimes across
    the app and also produces JSON strings without a timezone suffix (the
    browser then parses them as local time). This decorator normalises to UTC
    on both write and read.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _ensure_storage_dirs() -> None:
    settings = get_settings()
    Path(settings.STORAGE_ROOT).mkdir(parents=True, exist_ok=True)
    url = settings.DATABASE_URL
    if url.startswith("sqlite:///") and not url.endswith(":memory:"):
        db_path = Path(url.removeprefix("sqlite:///"))
        db_path.parent.mkdir(parents=True, exist_ok=True)


def _build_engine() -> Engine:
    settings = get_settings()
    url = settings.DATABASE_URL
    connect_args: dict[str, Any] = {}
    if url.startswith("sqlite"):
        # The worker thread and request threads both hold sessions; disable the
        # single-thread guard and rely on our module-level job lock for ordering.
        connect_args = {"check_same_thread": False}
    return create_engine(url, connect_args=connect_args, future=True)


_ensure_storage_dirs()
engine: Engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


@event.listens_for(Engine, "connect")
def _sqlite_enable_foreign_keys(dbapi_connection: Any, connection_record: Any) -> None:
    """Turn on `PRAGMA foreign_keys` for every new SQLite connection."""
    # The listener fires for every Engine; only run the pragma for SQLite drivers.
    if "sqlite" not in type(dbapi_connection).__module__:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped ORM session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def create_all() -> None:
    """Create every table declared under `app.models`. Never called at import time."""
    import app.models  # noqa: F401  Force model modules to register with Base.metadata.

    Base.metadata.create_all(engine)

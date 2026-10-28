"""Async engine, session factory, UTC datetime type and the declarative base."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy import DateTime, MetaData, case, literal, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql.dml import Insert
from sqlalchemy.types import TypeDecorator

from campus_parking.core.settings import Settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC in and out, on Postgres (timestamptz) and SQLite (naive storage)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime: pass an aware value")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def insert_ignore(dialect_name: str, entity: type[Base]) -> Insert:
    """INSERT ... ON CONFLICT DO NOTHING for the dialects the project runs on.

    Chain `.values(...)` and, when the caller needs the rows that were really inserted,
    `.returning(...)`: skipped rows are not returned on either dialect.
    """
    if dialect_name == "postgresql":
        return pg_insert(entity).on_conflict_do_nothing()
    if dialect_name == "sqlite":
        return sqlite_insert(entity).on_conflict_do_nothing()
    raise NotImplementedError(f"insert_ignore does not support {dialect_name!r}")


def never_backwards(column: Any, value: datetime) -> Any:
    """SQL expression for `column = max(column, value)` that works on Postgres and SQLite."""
    # Typed explicitly: an untyped datetime parameter would be bound as a naive timestamp.
    bound = literal(value, UTCDateTime())
    return case((or_(column.is_(None), column < bound), bound), else_=column)


def create_engine(settings: Settings) -> AsyncEngine:
    # hide_parameters: a failing statement never logs the values bound to it.
    kwargs: dict[str, Any] = {"hide_parameters": True}
    if not settings.database_url.startswith("sqlite"):
        kwargs["pool_pre_ping"] = True
    return create_async_engine(settings.database_url, **kwargs)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, from the app's sessionmaker."""
    async with request.app.state.sessionmaker() as session:
        yield session

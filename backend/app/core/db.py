"""Database engine, declarative base, mixins and the unit of work.

The transaction boundary lives in the service layer: routers never commit.
`UnitOfWork` opens one transaction per use-case so that a cross-module write —
GRN approval touching stock, budget, GL, outbox and audit — either lands
entirely or not at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Self
from uuid import UUID

from sqlalchemy import DateTime, Integer, MetaData, event, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, declared_attr, mapped_column

from app.core.config import settings
from app.core.types import uuid7

# -----------------------------------------------------------------------------
# Naming convention
# -----------------------------------------------------------------------------
# Without this, Alembic autogenerate produces unnamed constraints that cannot be
# dropped on downgrade, and index names differ between machines.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(AsyncAttrs, DeclarativeBase):
    """Declarative base for every ORM model."""

    metadata = metadata

    # Models opt in to automatic audit-log capture.
    __audited__: bool = False
    # Columns never written to the audit log (secrets, tokens, hashes).
    __audit_exclude__: tuple[str, ...] = ()

    def __repr__(self) -> str:
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} {pk}>"


# -----------------------------------------------------------------------------
# Mixins
# -----------------------------------------------------------------------------


class UUIDPrimaryKey:
    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid7, sort_order=-100
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, sort_order=90
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        sort_order=91,
    )


class ActorMixin:
    """Who created and last changed the row.

    Nullable because seeds and system jobs legitimately have no user; those
    rows are identified in the audit log by `actor_label` instead.
    """

    created_by_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True, sort_order=92
    )
    updated_by_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True, sort_order=93
    )


class VersionMixin:
    """Optimistic locking for aggregates edited concurrently.

    Incremented by the service layer on each update and checked against the
    client's `If-Match` value, so two users editing the same purchase order
    cannot silently overwrite one another.
    """

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, sort_order=94)


class SoftDeleteMixin:
    """Master data only.

    Transactional records are never soft-deleted — they are cancelled or
    reversed, so the ledger stays complete. See docs/00 D-06.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True, sort_order=95
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class CompanyScoped:
    """Every business table carries the company it belongs to.

    Present from day one even though v1 runs a single company, so enabling
    multi-company is a configuration change rather than a migration of every
    table. See docs/00 decision D-01/A-08.
    """

    @declared_attr
    @classmethod
    def company_id(cls) -> Mapped[UUID]:
        from sqlalchemy import ForeignKey

        return mapped_column(
            PgUUID(as_uuid=True),
            ForeignKey("companies.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
            sort_order=-99,
        )


class BaseModel(Base, UUIDPrimaryKey, TimestampMixin, ActorMixin):
    """Convenience base: id + timestamps + actor."""

    __abstract__ = True


class CompanyModel(BaseModel, CompanyScoped):
    """Convenience base for company-scoped business tables."""

    __abstract__ = True


class MasterDataModel(CompanyModel, SoftDeleteMixin, VersionMixin):
    """Convenience base for master data: soft-deletable and version-tracked."""

    __abstract__ = True
    __audited__ = True


# -----------------------------------------------------------------------------
# Engine and sessions
# -----------------------------------------------------------------------------

engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle_seconds,
    pool_pre_ping=True,
    # asyncpg caches prepared statements per connection. Behind PgBouncer in
    # transaction mode a connection is not stable, so the cache must be off.
    # Harmless without PgBouncer; required with it (Stage 2 onward).
    connect_args={"statement_cache_size": 0},
)

SessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency for read-only handlers.

    Write handlers should depend on `UnitOfWork` instead, which owns the
    transaction and runs the audit hook.
    """
    async with SessionFactory() as session:
        try:
            yield session
        finally:
            await session.close()


class UnitOfWork:
    """One transaction per use-case.

    Usage::

        async with UnitOfWork() as uow:
            po = await service.approve(po_id, uow)
        # committed here; audit rows and outbox events written inside

    Nothing commits on the way out of an exception, and the audit hook runs
    before flush so it sees the old values.
    """

    session: AsyncSession

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._external = session is not None
        self.session = session or SessionFactory()
        self._committed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            if exc_type is None and not self._committed:
                await self.commit()
            elif exc_type is not None:
                await self.rollback()
        finally:
            if not self._external:
                await self.session.close()

    async def commit(self) -> None:
        await self.session.commit()
        self._committed = True

    async def rollback(self) -> None:
        await self.session.rollback()

    async def flush(self) -> None:
        await self.session.flush()


async def get_uow() -> AsyncIterator[UnitOfWork]:
    """FastAPI dependency providing a transactional unit of work."""
    async with SessionFactory() as session, UnitOfWork(session) as uow:
        yield uow


# -----------------------------------------------------------------------------
# Identifier assignment
# -----------------------------------------------------------------------------


@event.listens_for(Base, "init", propagate=True)
def _assign_id_at_construction(target: Any, _args: Any, kwargs: dict[str, Any]) -> None:
    """Give every new instance its UUID before `__init__` runs.

    A `default=` column default is only evaluated at INSERT, which is too late
    for anything that inspects the object earlier — in particular the audit
    writer, which runs on `before_flush` and would otherwise record
    `entity_id = NULL`, leaving the audit row unlinkable to the record it
    describes.

    An explicitly passed `id` always wins.
    """
    if isinstance(target, UUIDPrimaryKey) and "id" not in kwargs:
        kwargs["id"] = uuid7()


# -----------------------------------------------------------------------------
# Actor stamping
# -----------------------------------------------------------------------------


@event.listens_for(Session, "before_flush")
def _stamp_actor(session: Session, _flush_context: Any, _instances: Any) -> None:
    """Fill created_by_id / updated_by_id from the request context.

    Registered on the synchronous `Session` because that is what AsyncSession
    drives underneath; ORM flush events are not emitted on AsyncSession.
    """
    from app.core.context import current_context

    user_id = current_context().user_id
    if user_id is None:
        return
    for obj in session.new:
        if isinstance(obj, ActorMixin) and obj.created_by_id is None:
            obj.created_by_id = user_id
            obj.updated_by_id = user_id
    for obj in session.dirty:
        if isinstance(obj, ActorMixin) and session.is_modified(obj):
            obj.updated_by_id = user_id


async def check_database() -> bool:
    """Readiness probe helper."""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 — the probe must never raise
        return False


async def dispose_engine() -> None:
    await engine.dispose()

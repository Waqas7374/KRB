"""Seed infrastructure.

Every seeder is **idempotent**: it upserts by natural key, so `make seed` can be
run repeatedly against a live database without duplicating anything or
clobbering edits made through the UI.

Seeds run inside `system_context()`, so the audit rows they produce are labelled
as machine-originated rather than attributed to a person who was not there.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Base
from app.core.logging import get_logger

log = get_logger("seed")

SeedFn = Callable[[AsyncSession], Awaitable["SeedResult"]]


@dataclass
class SeedResult:
    name: str
    created: int = 0
    updated: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return (
            f"{self.name:<28} created={self.created:<4} "
            f"updated={self.updated:<4} unchanged={self.skipped}"
        )

    def __iadd__(self, other: SeedResult) -> SeedResult:
        self.created += other.created
        self.updated += other.updated
        self.skipped += other.skipped
        return self


async def upsert[T: Base](
    session: AsyncSession,
    model: type[T],
    *,
    match: dict[str, Any],
    values: dict[str, Any],
    result: SeedResult,
    update_existing: bool = True,
    protected_fields: Sequence[str] = (),
) -> T:
    """Find a row by `match`, creating or updating it to `values`.

    `protected_fields` are set only on creation — used for anything an
    administrator may legitimately have changed since the last seed run
    (a role's description, a project's status), so re-seeding does not undo
    real configuration.
    """
    stmt = select(model)
    for column, value in match.items():
        stmt = stmt.where(getattr(model, column) == value)

    existing = (await session.execute(stmt)).scalar_one_or_none()

    if existing is None:
        instance = model(**match, **values)
        session.add(instance)
        await session.flush()
        result.created += 1
        return instance

    if not update_existing:
        result.skipped += 1
        return existing

    changed = False
    for field, value in values.items():
        if field in protected_fields:
            continue
        if getattr(existing, field, None) != value:
            setattr(existing, field, value)
            changed = True

    if changed:
        result.updated += 1
        await session.flush()
    else:
        result.skipped += 1
    return existing


async def find_id(session: AsyncSession, model: type[Base], **match: Any) -> UUID | None:
    stmt = select(model.id)  # type: ignore[attr-defined]
    for column, value in match.items():
        stmt = stmt.where(getattr(model, column) == value)
    found: UUID | None = (await session.execute(stmt)).scalar_one_or_none()
    return found

"""Generic scoped repository for master data.

Used only where it pays for itself (docs/01 §2): simple company-scoped master
data with a code and a name. Aggregates with real behaviour — inventory, GL,
approvals, deliveries — get bespoke repositories, because a generic one would
hide the queries that matter.

Every read goes through `scope_filter`, so authorisation is a WHERE clause and
pagination counts stay truthful.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.db import Base
from app.core.errors import DuplicateError, NotFoundError, VersionConflictError
from app.core.pagination import PageParams, apply_sort
from app.core.scoping import scope_filter
from app.core.types import utcnow


class ScopedRepository[T: Base]:
    """CRUD over one company-scoped model, with scope filtering built in."""

    def __init__(
        self,
        session: AsyncSession,
        model: type[T],
        *,
        entity_name: str,
        sortable: set[str],
        searchable: tuple[str, ...] = (),
        default_sort: str = "-created_at",
    ) -> None:
        self.session = session
        self.model = model
        self.entity_name = entity_name
        self.sortable = sortable
        self.searchable = searchable
        self.default_sort = default_sort

    # -- Queries -------------------------------------------------------------

    def base_query(self, ctx: AccessContext, permission: str) -> Select[Any]:
        stmt = select(self.model)
        stmt = scope_filter(stmt, self.model, ctx, permission)
        # Soft-deleted master data is hidden by default; history lives in the
        # audit log, not in every list screen.
        if hasattr(self.model, "deleted_at"):
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return stmt

    def apply_search(self, stmt: Select[Any], term: str | None) -> Select[Any]:
        if not term or not self.searchable:
            return stmt
        pattern = f"%{term.strip()}%"
        clauses = [getattr(self.model, field).ilike(pattern) for field in self.searchable]
        return stmt.where(or_(*clauses))

    def apply_filters(self, stmt: Select[Any], filters: dict[str, Any]) -> Select[Any]:
        """Apply equality and `in` filters for declared columns only."""
        for field, value in filters.items():
            if value is None:
                continue
            column = getattr(self.model, field, None)
            if column is None:
                continue
            if isinstance(value, list | tuple | set):
                values = [v for v in value if v is not None]
                if values:
                    stmt = stmt.where(column.in_(values))
            else:
                stmt = stmt.where(column == value)
        return stmt

    async def list(
        self,
        ctx: AccessContext,
        permission: str,
        *,
        page: PageParams,
        search: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> tuple[list[T], int]:
        stmt = self.base_query(ctx, permission)
        stmt = self.apply_filters(stmt, filters or {})
        stmt = self.apply_search(stmt, search)

        total = (
            await self.session.scalar(
                select(func.count()).select_from(stmt.order_by(None).subquery())
            )
        ) or 0

        stmt = apply_sort(
            stmt, self.model, page.sort, allowed=self.sortable, default=self.default_sort
        )
        rows = (
            (await self.session.execute(stmt.limit(page.limit).offset(page.offset)))
            .scalars()
            .unique()
            .all()
        )
        return list(rows), total

    async def get(self, ctx: AccessContext, permission: str, record_id: UUID) -> T:
        """Fetch by id, within scope.

        Out of scope yields a 404 rather than a 403: confirming that a record
        exists leaks vendor lists and document numbering sequences.
        """
        stmt = self.base_query(ctx, permission).where(self.model.id == record_id)  # type: ignore[attr-defined]
        row: T | None = (await self.session.execute(stmt)).scalars().unique().one_or_none()
        if row is None:
            raise NotFoundError(self.entity_name, record_id)
        return row

    async def get_for_update(self, ctx: AccessContext, permission: str, record_id: UUID) -> T:
        """Fetch and lock, for a read-modify-write inside one transaction."""
        stmt = (
            self.base_query(ctx, permission)
            .where(self.model.id == record_id)  # type: ignore[attr-defined]
            .with_for_update()
        )
        row: T | None = (await self.session.execute(stmt)).scalars().unique().one_or_none()
        if row is None:
            raise NotFoundError(self.entity_name, record_id)
        return row

    # -- Writes --------------------------------------------------------------

    async def assert_code_available(
        self, company_id: UUID, field: str, value: str, *, exclude_id: UUID | None = None
    ) -> None:
        """Pre-check a unique code so the user gets a field error, not a 409.

        The database constraint is still the authority — this is about the
        message, not the guarantee.
        """
        # mypy cannot see `id` / `company_id` through the TypeVar; every model
        # this repository is used with carries both by construction (CompanyModel).
        column = getattr(self.model, field)
        stmt = select(self.model.id).where(  # type: ignore[attr-defined]
            self.model.company_id == company_id,  # type: ignore[attr-defined]
            column == value,
        )
        if exclude_id is not None:
            stmt = stmt.where(self.model.id != exclude_id)  # type: ignore[attr-defined]
        if (await self.session.execute(stmt)).first() is not None:
            raise DuplicateError(self.entity_name, field, value)

    async def create(self, **values: Any) -> T:
        instance = self.model(**values)
        self.session.add(instance)
        await self.session.flush()
        return instance

    def apply_update(
        self, instance: T, values: dict[str, Any], *, expected_version: int | None = None
    ) -> bool:
        """Apply a partial update. Returns True when something changed.

        `expected_version` implements optimistic concurrency: two people editing
        the same vendor cannot silently overwrite one another.
        """
        if expected_version is not None and hasattr(instance, "version"):
            actual = instance.version
            if actual != expected_version:
                raise VersionConflictError(
                    f"This {self.entity_name.lower()} was changed by someone else "
                    f"(version {actual}, you had {expected_version}). Reload and try again."
                )

        changed = False
        for field, value in values.items():
            if not hasattr(instance, field):
                continue
            if getattr(instance, field) != value:
                setattr(instance, field, value)
                changed = True

        if changed and hasattr(instance, "version"):
            instance.version += 1
        return changed

    async def soft_delete(self, instance: T) -> None:
        """Mark master data deleted.

        Only ever called for master data. Transactional records are cancelled or
        reversed, never deleted, so the ledger stays complete (docs/00 D-06).
        """
        if not hasattr(instance, "deleted_at"):
            raise RuntimeError(
                f"{type(instance).__name__} is transactional and must not be deleted; "
                "cancel or reverse it instead."
            )
        instance.deleted_at = utcnow()
        await self.session.flush()

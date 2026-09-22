"""Pagination, sorting and the collection response envelope.

Two modes, both with the same envelope:

* offset — for screens with page numbers, capped at 10 000 rows deep because a
  deep OFFSET on a growing table is a slow full scan waiting to happen;
* cursor — for high-volume, append-mostly data (deliveries, stock ledger, audit
  log) where the caller pages forward.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from typing import Any, Self

from fastapi import Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Select, asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ValidationError

MAX_LIMIT = 200
MAX_OFFSET = 10_000


class PageParams(BaseModel):
    limit: int = Field(default=50, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0, le=MAX_OFFSET)
    cursor: str | None = None
    sort: str | None = None

    @field_validator("sort")
    @classmethod
    def _reject_injection(cls, value: str | None) -> str | None:
        if value is None:
            return None
        for field in value.split(","):
            name = field.strip().lstrip("-")
            if not name.replace("_", "").replace(".", "").isalnum():
                raise ValueError(f"Invalid sort field: {field}")
        return value


def page_params(
    limit: int = Query(50, ge=1, le=MAX_LIMIT, description="Rows per page"),
    offset: int = Query(0, ge=0, le=MAX_OFFSET, description="Rows to skip"),
    cursor: str | None = Query(None, description="Opaque forward cursor"),
    sort: str | None = Query(None, description="Comma-separated fields; prefix '-' for descending"),
) -> PageParams:
    """FastAPI dependency."""
    return PageParams(limit=limit, offset=offset, cursor=cursor, sort=sort)


class PageMeta(BaseModel):
    limit: int
    offset: int
    total: int | None = None
    has_more: bool = False
    next_cursor: str | None = None


class Page[T](BaseModel):
    items: list[T]
    page: PageMeta
    meta: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def of(
        cls,
        items: Sequence[T],
        *,
        params: PageParams,
        total: int | None = None,
        next_cursor: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Self:
        has_more = next_cursor is not None if total is None else params.offset + len(items) < total
        return cls(
            items=list(items),
            page=PageMeta(
                limit=params.limit,
                offset=params.offset,
                total=total,
                has_more=has_more,
                next_cursor=next_cursor,
            ),
            meta=extra or {},
        )


# -----------------------------------------------------------------------------
# Sorting
# -----------------------------------------------------------------------------


def apply_sort(
    stmt: Select[Any],
    model: type[Any],
    sort: str | None,
    *,
    allowed: set[str],
    default: str = "-created_at",
) -> Select[Any]:
    """Apply a validated sort clause.

    Only fields in `allowed` may be sorted on. An unknown field is a 422, not a
    silent fallback — a user who sorts by a column that quietly does nothing
    will export the wrong data and sign it.
    """
    spec = sort or default
    clauses = []
    for raw in spec.split(","):
        field = raw.strip()
        if not field:
            continue
        descending = field.startswith("-")
        name = field.lstrip("-")
        if name not in allowed:
            raise ValidationError(
                f"Cannot sort by '{name}'",
                errors=[
                    {
                        "field": "sort",
                        "code": "not_sortable",
                        "message": f"allowed: {', '.join(sorted(allowed))}",
                    }
                ],
            )
        column = getattr(model, name)
        clauses.append(desc(column) if descending else asc(column))

    # A stable tiebreaker, otherwise rows can repeat or vanish across pages.
    if hasattr(model, "id"):
        clauses.append(desc(model.id))
    return stmt.order_by(*clauses)


# -----------------------------------------------------------------------------
# Execution helpers
# -----------------------------------------------------------------------------


async def paginate(
    session: AsyncSession,
    stmt: Select[Any],
    params: PageParams,
    *,
    count_total: bool = True,
) -> tuple[Sequence[Any], int | None]:
    """Run an offset-paginated query, returning (rows, total)."""
    total: int | None = None
    if count_total:
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = await session.scalar(count_stmt) or 0

    result = await session.execute(stmt.limit(params.limit).offset(params.offset))
    return result.scalars().all(), total


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded: dict[str, Any] = json.loads(base64.urlsafe_b64decode(padded))
        return decoded
    except Exception as exc:
        raise BadRequestError("Malformed cursor") from exc

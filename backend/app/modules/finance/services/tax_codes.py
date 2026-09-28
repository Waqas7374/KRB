"""Tax codes (docs/12 Q1): one rate, of one kind, on one thing. A sales-tax
code prices a document line; a withholding code (with its own `section_code`)
tells a Phase 4d payment how much of what it pays out it must hold back."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import PermissionDeniedError, VersionConflictError
from app.core.pagination import PageParams
from app.modules.finance.domain.enums import TaxAppliesTo, TaxType
from app.modules.finance.models import TaxCode

PERM_VIEW = "finance.coa.view"
PERM_MANAGE = "finance.coa.manage"


def repository(session: AsyncSession) -> ScopedRepository[TaxCode]:
    return ScopedRepository(
        session,
        TaxCode,
        entity_name="Tax code",
        sortable={"code", "name", "tax_type", "rate_pct", "created_at", "updated_at"},
        searchable=("code", "name", "section_code"),
        default_sort="code",
    )


@dataclass(frozen=True, slots=True)
class TaxCodeInput:
    code: str
    name: str
    tax_type: TaxType
    rate_pct: Decimal
    applies_to: TaxAppliesTo
    section_code: str | None = None
    is_active: bool = True


async def create(session: AsyncSession, ctx: AccessContext, data: TaxCodeInput) -> TaxCode:
    if not ctx.has(PERM_MANAGE):
        raise PermissionDeniedError(PERM_MANAGE)
    await repository(session).assert_code_available(ctx.company_id, "code", data.code)
    row = TaxCode(
        company_id=ctx.company_id,
        code=data.code,
        name=data.name,
        tax_type=data.tax_type.value,
        rate_pct=data.rate_pct,
        section_code=data.section_code,
        applies_to=data.applies_to.value,
        is_active=data.is_active,
        created_by_id=ctx.user_id,
    )
    session.add(row)
    await session.flush()
    return row


@dataclass(frozen=True, slots=True)
class TaxCodeEdit:
    name: str | None = None
    rate_pct: Decimal | None = None
    section_code: str | None = None
    is_active: bool | None = None


async def update(
    session: AsyncSession,
    ctx: AccessContext,
    tax_code_id: UUID,
    data: TaxCodeEdit,
    *,
    expected_version: int | None,
) -> TaxCode:
    row = await repository(session).get_for_update(ctx, PERM_MANAGE, tax_code_id)
    if expected_version is not None and row.version != expected_version:
        raise VersionConflictError(
            f"{row.code} was changed by someone else (version {row.version}, you had "
            f"{expected_version}). Reload and try again."
        )
    if data.name is not None:
        row.name = data.name
    if data.rate_pct is not None:
        row.rate_pct = data.rate_pct
    if data.section_code is not None:
        row.section_code = data.section_code or None
    if data.is_active is not None:
        row.is_active = data.is_active
    row.updated_by_id = ctx.user_id
    row.version += 1
    await session.flush()
    return row


async def list_tax_codes(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[TaxCode], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, tax_code_id: UUID) -> TaxCode:
    return await repository(session).get(ctx, PERM_VIEW, tax_code_id)


async def all_active(session: AsyncSession, ctx: AccessContext) -> list[TaxCode]:
    if not ctx.has(PERM_VIEW):
        return []
    rows, _ = await repository(session).list(
        ctx,
        PERM_VIEW,
        page=PageParams(offset=0, limit=200),
        search=None,
        filters={"is_active": True},
    )
    return rows

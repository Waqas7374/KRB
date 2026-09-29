"""The company's own bank accounts (docs/02 §8) — where a payment's own
`gl_account_id` credit leg comes from. Never a vendor's bank details; those
are a vendor's own, separately permissioned concern."""

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
from app.modules.finance.models import BankAccount

PERM_VIEW = "finance.coa.view"
PERM_MANAGE = "finance.coa.manage"


def repository(session: AsyncSession) -> ScopedRepository[BankAccount]:
    return ScopedRepository(
        session,
        BankAccount,
        entity_name="Bank account",
        sortable={"account_title", "bank_name", "created_at", "updated_at"},
        searchable=("account_title", "account_no", "bank_name"),
        default_sort="account_title",
    )


@dataclass(frozen=True, slots=True)
class BankAccountInput:
    account_title: str
    account_no: str
    bank_name: str
    gl_account_id: UUID
    iban: str | None = None
    currency_code: str = "PKR"
    opening_balance: Decimal = Decimal(0)
    is_active: bool = True


async def create(session: AsyncSession, ctx: AccessContext, data: BankAccountInput) -> BankAccount:
    if not ctx.has(PERM_MANAGE):
        raise PermissionDeniedError(PERM_MANAGE)
    await repository(session).assert_code_available(ctx.company_id, "account_no", data.account_no)
    row = BankAccount(
        company_id=ctx.company_id,
        account_title=data.account_title,
        account_no=data.account_no,
        iban=data.iban,
        bank_name=data.bank_name,
        currency_code=data.currency_code,
        gl_account_id=data.gl_account_id,
        opening_balance=data.opening_balance,
        is_active=data.is_active,
        created_by_id=ctx.user_id,
    )
    session.add(row)
    await session.flush()
    return row


@dataclass(frozen=True, slots=True)
class BankAccountEdit:
    account_title: str | None = None
    iban: str | None = None
    bank_name: str | None = None
    is_active: bool | None = None


async def update(
    session: AsyncSession,
    ctx: AccessContext,
    bank_account_id: UUID,
    data: BankAccountEdit,
    *,
    expected_version: int | None,
) -> BankAccount:
    row = await repository(session).get_for_update(ctx, PERM_MANAGE, bank_account_id)
    if expected_version is not None and row.version != expected_version:
        raise VersionConflictError(
            f"{row.account_title} was changed by someone else (version {row.version}, you had "
            f"{expected_version}). Reload and try again."
        )
    if data.account_title is not None:
        row.account_title = data.account_title
    if data.iban is not None:
        row.iban = data.iban or None
    if data.bank_name is not None:
        row.bank_name = data.bank_name
    if data.is_active is not None:
        row.is_active = data.is_active
    row.updated_by_id = ctx.user_id
    row.version += 1
    await session.flush()
    return row


async def list_accounts(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[BankAccount], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, bank_account_id: UUID) -> BankAccount:
    return await repository(session).get(ctx, PERM_VIEW, bank_account_id)


async def all_active(session: AsyncSession, ctx: AccessContext) -> list[BankAccount]:
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

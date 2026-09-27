"""The chart of accounts (docs/02 §8).

Only a leaf may be posted to (`is_postable`). An account becomes a group the
moment a first child is attached to it, and stays one — turning a group back
into a leaf would let someone post to an account that other accounts already
roll up into, which is exactly the ambiguity a chart of accounts exists to
avoid. A code, its type and its place in the tree are fixed at creation:
moving accounts around after they may have been posted to is not supported in
v1 — deactivate the old one and create a new one instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import BusinessRuleError, ValidationError, VersionConflictError
from app.core.pagination import PageParams
from app.core.types import uuid7
from app.modules.finance.domain.enums import AccountType
from app.modules.finance.models import Account, JournalEntryLine

PERM_VIEW = "finance.coa.view"
PERM_MANAGE = "finance.coa.manage"


def repository(session: AsyncSession) -> ScopedRepository[Account]:
    return ScopedRepository(
        session,
        Account,
        entity_name="Account",
        sortable={"code", "name", "account_type", "created_at", "updated_at"},
        searchable=("code", "name"),
        default_sort="code",
    )


def _fail(field: str, message: str) -> ValidationError:
    return ValidationError(
        message, errors=[{"field": field, "code": "invalid", "message": message}]
    )


@dataclass(frozen=True, slots=True)
class AccountInput:
    code: str
    name: str
    account_type: AccountType
    parent_id: UUID | None
    requires_project: bool = False
    requires_cost_center: bool = False
    description: str | None = None


async def _has_postings(session: AsyncSession, account_id: UUID) -> bool:
    return bool(
        await session.scalar(
            select(func.count())
            .select_from(JournalEntryLine)
            .where(JournalEntryLine.account_id == account_id)
        )
    )


async def create(session: AsyncSession, ctx: AccessContext, data: AccountInput) -> Account:
    repo = repository(session)
    await repo.assert_code_available(ctx.company_id, "code", data.code)

    parent = None
    if data.parent_id is not None:
        parent = await session.get(Account, data.parent_id)
        if parent is None or parent.company_id != ctx.company_id or parent.deleted_at:
            raise _fail("parent_id", "Unknown parent account")
        if await _has_postings(session, parent.id):
            raise BusinessRuleError(
                "parent_has_postings",
                f"{parent.code} already has journal lines posted to it, so it cannot become a "
                "group account. Choose a different parent, or create a new group above it.",
            )

    new_id = uuid7()
    path = f"{parent.path}.{new_id}" if parent else str(new_id)
    account = Account(
        id=new_id,
        company_id=ctx.company_id,
        code=data.code,
        name=data.name.strip(),
        account_type=data.account_type.value,
        normal_balance=data.account_type.normal_balance.value,
        parent_id=parent.id if parent else None,
        path=path,
        is_postable=True,
        requires_project=data.requires_project,
        requires_cost_center=data.requires_cost_center,
        description=data.description,
        created_by_id=ctx.user_id,
    )
    session.add(account)
    if parent is not None and parent.is_postable:
        parent.is_postable = False
        parent.updated_by_id = ctx.user_id
    await session.flush()
    return account


@dataclass(frozen=True, slots=True)
class AccountEdit:
    name: str | None = None
    description: str | None = None
    requires_project: bool | None = None
    requires_cost_center: bool | None = None
    is_active: bool | None = None


async def update(
    session: AsyncSession,
    ctx: AccessContext,
    account_id: UUID,
    data: AccountEdit,
    *,
    expected_version: int | None,
) -> Account:
    account = await repository(session).get_for_update(ctx, PERM_MANAGE, account_id)
    if expected_version is not None and account.version != expected_version:
        raise VersionConflictError(
            f"{account.code} was changed by someone else (version {account.version}, "
            f"you had {expected_version}). Reload and try again."
        )
    if data.name is not None:
        account.name = data.name.strip()
    if data.description is not None:
        account.description = data.description or None
    if data.requires_project is not None:
        account.requires_project = data.requires_project
    if data.requires_cost_center is not None:
        account.requires_cost_center = data.requires_cost_center
    if data.is_active is not None:
        account.is_active = data.is_active
    account.updated_by_id = ctx.user_id
    account.version += 1
    await session.flush()
    return account


async def list_accounts(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[Account], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, account_id: UUID) -> Account:
    return await repository(session).get(ctx, PERM_VIEW, account_id)


async def all_active(session: AsyncSession, ctx: AccessContext) -> list[Account]:
    """Every active account, for the tree screen and for pickers. The whole
    chart is never long enough to paginate (docs/02 §8: a few hundred rows at
    most), so this reads all of it rather than a page at a time."""
    if not ctx.has(PERM_VIEW):
        return []
    rows = await session.execute(
        select(Account)
        .where(Account.company_id == ctx.company_id, Account.deleted_at.is_(None))
        .order_by(Account.path)
    )
    return list(rows.scalars().all())


@dataclass(frozen=True, slots=True)
class AccountNode:
    account: Account
    children: list[AccountNode]


def tree(accounts: list[Account]) -> list[AccountNode]:
    """Assemble a flat, path-ordered list into a tree, in one pass."""
    nodes = {a.id: AccountNode(a, []) for a in accounts}
    roots: list[AccountNode] = []
    for account in accounts:
        node = nodes[account.id]
        parent = nodes.get(account.parent_id) if account.parent_id else None
        (parent.children if parent else roots).append(node)
    return roots

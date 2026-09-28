"""Sub-ledger → GL mapping, as configuration (docs/02 §8).

`resolve()` is the one function every posting caller (today: GRN receipts;
4c-4d will add vendor-invoice and payment events) goes through to find which
accounts a movement hits. Whether a rule applies is decided by the same
condition language business rules and approval workflows already use
(`core/conditions.py`), evaluated against a small, event-specific context —
for a GRN receipt, `is_stockable`, `is_po_backed` and `material_category_id`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import conditions
from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import (
    BusinessRuleError,
    PermissionDeniedError,
    ValidationError,
    VersionConflictError,
)
from app.core.pagination import PageParams
from app.modules.finance.domain.enums import JournalSourceType
from app.modules.finance.models import PostingRule
from app.modules.masterdata.services import material_lookup

PERM_VIEW = "finance.coa.view"
PERM_MANAGE = "finance.coa.manage"

# The context variables any posting rule's condition may use, across every
# event — validated the same way an approval workflow's condition is, so a
# typo is caught at save time rather than silently never matching. One shared
# set rather than one per event: simpler, and a condition that uses the wrong
# event's variable will still just never match rather than error, which the
# event mismatch on the rule's own `event` field already prevents.
CONDITION_VARIABLES = frozenset(
    {"is_stockable", "is_po_backed", "material_category_id", "direction"}
)


def repository(session: AsyncSession) -> ScopedRepository[PostingRule]:
    return ScopedRepository(
        session,
        PostingRule,
        entity_name="Posting rule",
        sortable={"source_type", "event", "priority", "created_at", "updated_at"},
        searchable=("event", "name"),
        default_sort="-priority",
    )


def _validate_condition(condition: Any) -> None:
    try:
        conditions.validate(condition, variables=CONDITION_VARIABLES)
    except conditions.ConditionError as exc:
        raise ValidationError(
            str(exc), errors=[{"field": "condition", "code": "invalid", "message": str(exc)}]
        ) from exc


@dataclass(frozen=True, slots=True)
class PostingRuleInput:
    source_type: JournalSourceType
    event: str
    debit_account_id: UUID
    credit_account_id: UUID
    name: str | None = None
    condition: Any | None = None
    priority: int = 0
    is_active: bool = True


async def create(session: AsyncSession, ctx: AccessContext, data: PostingRuleInput) -> PostingRule:
    if not ctx.has(PERM_MANAGE):
        raise PermissionDeniedError(PERM_MANAGE)
    if data.condition is not None:
        _validate_condition(data.condition)
    rule = PostingRule(
        company_id=ctx.company_id,
        source_type=data.source_type.value,
        event=data.event.strip(),
        name=data.name,
        condition=data.condition,
        debit_account_id=data.debit_account_id,
        credit_account_id=data.credit_account_id,
        priority=data.priority,
        is_active=data.is_active,
        created_by_id=ctx.user_id,
    )
    session.add(rule)
    await session.flush()
    return rule


@dataclass(frozen=True, slots=True)
class PostingRuleEdit:
    name: str | None = None
    condition: Any | None = None
    debit_account_id: UUID | None = None
    credit_account_id: UUID | None = None
    priority: int | None = None
    is_active: bool | None = None


async def update(
    session: AsyncSession,
    ctx: AccessContext,
    rule_id: UUID,
    data: PostingRuleEdit,
    *,
    expected_version: int | None,
) -> PostingRule:
    rule = await repository(session).get_for_update(ctx, PERM_MANAGE, rule_id)
    if expected_version is not None and rule.version != expected_version:
        raise VersionConflictError(
            f"This posting rule was changed by someone else (version {rule.version}, you had "
            f"{expected_version}). Reload and try again."
        )
    if data.name is not None:
        rule.name = data.name or None
    if data.condition is not None:
        _validate_condition(data.condition)
        rule.condition = data.condition
    if data.debit_account_id is not None:
        rule.debit_account_id = data.debit_account_id
    if data.credit_account_id is not None:
        rule.credit_account_id = data.credit_account_id
    if data.priority is not None:
        rule.priority = data.priority
    if data.is_active is not None:
        rule.is_active = data.is_active
    rule.updated_by_id = ctx.user_id
    rule.version += 1
    await session.flush()
    return rule


async def list_rules(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[PostingRule], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, rule_id: UUID) -> PostingRule:
    return await repository(session).get(ctx, PERM_VIEW, rule_id)


async def resolve(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    source_type: JournalSourceType,
    event: str,
    context: dict[str, Any],
) -> PostingRule | None:
    """The highest-priority active rule for `source_type`/`event` whose
    condition matches `context`; a rule with no condition always matches. Ties
    on priority break on the older rule, so the first one an accountant wrote
    keeps winning until they explicitly reprioritise."""
    rows = (
        (
            await session.execute(
                select(PostingRule).where(
                    PostingRule.company_id == ctx.company_id,
                    PostingRule.source_type == source_type.value,
                    PostingRule.event == event,
                    PostingRule.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    matching = [r for r in rows if r.condition is None or conditions.matches(r.condition, context)]
    if not matching:
        return None
    matching.sort(key=lambda r: (r.priority, -r.created_at.timestamp()), reverse=True)
    return matching[0]


async def resolve_receipt_account(
    session: AsyncSession, ctx: AccessContext, *, material_id: UUID, is_po_backed: bool
) -> PostingRule:
    """The debit/credit pair a GRN line's receipt hits, for both the journal
    entry it posts and the budget line its value is tracked against — the same
    rule serves both, so a receipt's GL account and its budget account can
    never disagree."""
    material = (
        await material_lookup.materials(
            session, company_id=ctx.company_id, material_ids={material_id}
        )
    )[material_id]
    rule = await resolve(
        session,
        ctx,
        source_type=JournalSourceType.GRN,
        event="RECEIPT",
        context={
            "is_stockable": material.is_stockable,
            "is_po_backed": is_po_backed,
            "material_category_id": str(material.category_id) if material.category_id else None,
        },
    )
    if rule is None:
        kind = "stockable" if material.is_stockable else "non-stockable"
        backing = "against a purchase order" if is_po_backed else "bought without one"
        raise BusinessRuleError(
            "posting_rule_missing",
            f"No posting rule is set up for {kind} materials {backing}. Ask finance to add one "
            "under Posting Rules before this can post.",
        )
    return rule


async def resolve_or_fail(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    source_type: JournalSourceType,
    event: str,
    context: dict[str, Any],
    what: str,
) -> PostingRule:
    """`resolve()`, but posting is what this is for — a stock issue or an
    adjustment has nothing else to fall back on the way a GRN receipt's
    budget matching does, so a missing rule is a clear 422 here rather than
    something a caller has to remember to check for."""
    rule = await resolve(session, ctx, source_type=source_type, event=event, context=context)
    if rule is None:
        raise BusinessRuleError(
            "posting_rule_missing",
            f"No posting rule is set up for {what}. Ask finance to add one under Posting Rules "
            "before this can post.",
        )
    return rule

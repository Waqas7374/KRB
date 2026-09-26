"""Resolve a rule for a context.

The one entry point every feature uses: `resolve(...)` returns the winning
`RuleView` (value plus `snapshot()`) or None. Callers decide what "no rule"
means — a tonnage check with no rule simply does not flag.

Resolution reads the type's rules straight from the database. A cache can be
put behind this function later (docs/05 mentions Redis keyed by type and date,
invalidated on any write) without touching a single caller, which is why
nothing outside this module builds its own query.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import utcnow
from app.modules.rules.domain import resolution
from app.modules.rules.domain.enums import RuleType
from app.modules.rules.domain.resolution import RuleView, Verdict
from app.modules.rules.models import BusinessRule

__all__ = ["RuleView", "Verdict", "explain", "load", "resolve", "view"]


def view(row: BusinessRule) -> RuleView:
    return RuleView(
        id=row.id,
        rule_type=row.rule_type,
        name=row.name,
        scope=dict(row.scope or {}),
        condition=row.condition,
        value=dict(row.value or {}),
        priority=row.priority,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        is_active=row.is_active,
        created_at=row.created_at,
    )


async def load(
    session: AsyncSession, company_id: UUID, rule_type: RuleType | str
) -> list[RuleView]:
    rows = await session.execute(
        select(BusinessRule).where(
            BusinessRule.company_id == company_id, BusinessRule.rule_type == str(rule_type)
        )
    )
    return [view(r) for r in rows.scalars()]


async def resolve(
    session: AsyncSession,
    *,
    company_id: UUID,
    rule_type: RuleType,
    context: Mapping[str, Any],
    at: date | None = None,
) -> RuleView | None:
    """The most specific active rule of `rule_type` for `context` on `at`."""
    rules = await load(session, company_id, rule_type)
    return resolution.pick(rules, context, at or utcnow().date())


async def explain(
    session: AsyncSession,
    *,
    company_id: UUID,
    rule_type: RuleType,
    context: Mapping[str, Any],
    at: date | None = None,
) -> tuple[RuleView | None, list[Verdict]]:
    rules = await load(session, company_id, rule_type)
    return resolution.explain(rules, context, at or utcnow().date())

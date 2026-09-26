"""Default business rules (docs/05 §1 "Seeded defaults").

Company-wide defaults only. They are seeded when a rule type has no
company-wide rule at all, so an administrator's own default is never replaced
or duplicated by re-running the seeder. Tonnage limits are deliberately not
seeded: the default per truck type lives on the truck type itself.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import system_access_context
from app.modules.org.models import Company
from app.modules.rules.domain.enums import RuleType
from app.modules.rules.models import BusinessRule
from app.modules.rules.services import rule_service
from app.seeds.registry import SeedResult

# Early enough that resolving "as of" any historical delivery still finds them.
_EFFECTIVE_FROM = date(2020, 1, 1)

DEFAULTS: tuple[tuple[RuleType, str, dict[str, Any]], ...] = (
    (RuleType.GEOFENCE_RADIUS, "Default geofence radius", {"radius_m": 500}),
    (RuleType.QTY_TOLERANCE, "Default quantity tolerance", {"pct": "2", "abs": "0.5"}),
    (RuleType.PRICE_TOLERANCE, "Default price tolerance", {"pct": "0"}),
    (RuleType.CLOCK_SKEW_MAX, "Default clock skew", {"seconds": 900}),
    (RuleType.DUPLICATE_WINDOW, "Default duplicate window", {"minutes": 20}),
    (RuleType.LATE_SUBMISSION, "Default late-submission age", {"max_age_days": 2}),
)


async def seed_rules(session: AsyncSession, company: Company) -> SeedResult:
    result = SeedResult("business rules")
    ctx = system_access_context(company.id, company.id)
    for rule_type, name, value in DEFAULTS:
        existing = (
            await session.execute(
                select(BusinessRule.id, BusinessRule.scope).where(
                    BusinessRule.company_id == company.id,
                    BusinessRule.rule_type == rule_type.value,
                )
            )
        ).all()
        if any(not scope for _, scope in existing):
            result.skipped += 1
            continue
        rule = await rule_service.create(
            session,
            ctx,
            rule_service.RuleInput(
                rule_type=rule_type,
                name=name,
                description="Seeded default",
                scope={},
                condition=None,
                value=value,
                priority=0,
                effective_from=_EFFECTIVE_FROM,
                effective_to=None,
            ),
        )
        # A seeded row has no human author; the placeholder id must not linger.
        rule.created_by_id = None
        result.created += 1
    return result

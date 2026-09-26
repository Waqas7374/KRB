"""The business rules a phone can use to warn before it sends (docs/06 §4).

The server re-evaluates everything on ingest and is the authority; these let the
app say "this looks overweight" while the driver is still standing there.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sync import FeedRow
from app.modules.rules.domain.enums import RuleType
from app.modules.rules.models import BusinessRule

DEVICE_RULE_TYPES = (
    RuleType.TONNAGE_MAX.value,
    RuleType.GEOFENCE_RADIUS.value,
    RuleType.CLOCK_SKEW_MAX.value,
    RuleType.DUPLICATE_WINDOW.value,
    RuleType.LATE_SUBMISSION.value,
)


async def changes(
    session: AsyncSession, *, company_id: UUID, since: int, limit: int
) -> list[FeedRow]:
    rows = (
        (
            await session.execute(
                select(BusinessRule)
                .where(
                    BusinessRule.company_id == company_id,
                    BusinessRule.rule_type.in_(DEVICE_RULE_TYPES),
                    BusinessRule.server_seq.is_not(None),
                    BusinessRule.server_seq > since,
                )
                .order_by(BusinessRule.server_seq)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        FeedRow(
            "rules",
            r.id,
            int(r.server_seq or 0),
            # An inactive rule is as good as gone to the device.
            deleted=not r.is_active,
            data={
                "rule_type": r.rule_type,
                "scope": dict(r.scope or {}),
                "value": dict(r.value or {}),
                "priority": r.priority,
                "effective_from": r.effective_from.isoformat(),
                "effective_to": r.effective_to.isoformat() if r.effective_to else None,
            },
        )
        for r in rows
    ]

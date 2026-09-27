"""Approval limits: who may authorise *how much* (docs/04 §4).

Workflows decide who must sign; limits decide whether a given signer is
allowed to sign this much. They are `APPROVAL_LIMIT` rules scoped by role (and
optionally document type, project, site).

How a limit is worked out for a person:

* consider only the roles they hold, through grants covering the document,
  that actually carry the document's approve permission — a role that cannot
  approve purchase orders must not widen a purchase-order limit;
* each such role has the limit its most specific rule gives, or **none**;
* a role with no rule is unlimited, so the person is unlimited — introducing
  limits for one role never quietly restricts another;
* otherwise the person may approve up to their largest limit.

With no limit rules at all nothing changes, which is why this can ship before
anyone has decided what the limits are.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import utcnow
from app.modules.access.services import approver_lookup
from app.modules.rules.domain.enums import RuleType
from app.modules.rules.services import resolver


@dataclass(frozen=True, slots=True)
class LimitCheck:
    allowed: bool
    limit: Decimal | None  # None: unlimited
    currency: str | None = None
    rule_snapshot: dict[str, object] | None = None

    def message(self, doc_label: str, amount: Decimal, *, noun: str = "approval limit") -> str:
        assert self.limit is not None
        return (
            f"This {doc_label} is {self.currency or ''} {amount:,.2f}, above your {noun} "
            f"of {self.currency or ''} {self.limit:,.2f}. It needs someone with a higher limit."
        ).replace("  ", " ")


UNLIMITED = LimitCheck(allowed=True, limit=None)


async def check(
    session: AsyncSession,
    *,
    company_id: UUID,
    user_id: UUID,
    doc_type: str,
    approve_permission: str,
    amount: Decimal | None,
    project_id: UUID | None,
    site_id: UUID | None,
    department_id: UUID | None = None,
    on: date | None = None,
    rule_type: RuleType = RuleType.APPROVAL_LIMIT,
) -> LimitCheck:
    if amount is None:
        return UNLIMITED
    today = on or utcnow().date()
    roles = [
        r
        for r in await approver_lookup.roles_held(
            session,
            company_id=company_id,
            user_id=user_id,
            project_id=project_id,
            site_id=site_id,
            department_id=department_id,
            on=today,
        )
        if approve_permission in r.permissions
    ]
    if not roles:
        # Eligibility (permission and scope) is the engine's question, not this one.
        return UNLIMITED

    best: tuple[Decimal, str, dict[str, object]] | None = None
    for role in roles:
        rule = await resolver.resolve(
            session,
            company_id=company_id,
            rule_type=rule_type,
            context={
                "role_id": role.id,
                "doc_type": doc_type,
                "project_id": project_id,
                "site_id": site_id,
                "amount": amount,
            },
            at=today,
        )
        if rule is None:
            return UNLIMITED
        limit = Decimal(str(rule.value["limit"]))
        if best is None or limit > best[0]:
            best = (limit, str(rule.value.get("currency", "PKR")), dict(rule.snapshot()))
    assert best is not None
    limit, currency, snapshot = best
    return LimitCheck(
        allowed=amount <= limit, limit=limit, currency=currency, rule_snapshot=snapshot
    )

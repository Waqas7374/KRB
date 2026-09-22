"""Role-grant queries exposed to other modules.

The resolved `AccessContext` answers "may this person do X". This service
answers the different question "what roles does this person hold, and where" —
which is what a profile screen and an access-review report need.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.access.models import Role, UserRoleGrant


@dataclass(frozen=True, slots=True)
class GrantSummary:
    role_code: str
    role_name: str
    scope_type: str
    scope_id: UUID | None
    valid_from: date | None
    valid_to: date | None
    is_read_only: bool


async def list_active_grants(session: AsyncSession, user_id: UUID) -> list[GrantSummary]:
    """Every live grant for a user, newest role first.

    Revoked grants are excluded; their history is in the audit log, which is
    where an access review should look for what someone *used* to hold.
    """
    rows = (
        (
            await session.execute(
                select(UserRoleGrant, Role)
                .join(Role, Role.id == UserRoleGrant.role_id)
                .where(
                    UserRoleGrant.user_id == user_id,
                    UserRoleGrant.revoked_at.is_(None),
                )
                .order_by(Role.code)
            )
        )
        .tuples()
        .all()
    )

    return [
        GrantSummary(
            role_code=role.code,
            role_name=role.name,
            scope_type=grant.scope_type,
            scope_id=grant.scope_id,
            valid_from=grant.valid_from,
            valid_to=grant.valid_to,
            is_read_only=role.is_read_only,
        )
        for grant, role in rows
    ]

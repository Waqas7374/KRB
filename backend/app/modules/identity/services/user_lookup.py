"""Small identity lookups other modules need.

Exposed so a module like `access` can grant or revoke a role — which means
reading a user and bumping their `permissions_version` — without importing
`identity`'s ORM models directly. That boundary is enforced by
tests/unit/test_architecture.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.identity.models import User


@dataclass(frozen=True, slots=True)
class UserSummary:
    id: UUID
    full_name: str
    permissions_version: int


async def get_user_summary(
    session: AsyncSession, *, user_id: UUID, company_id: UUID
) -> UserSummary:
    user = (
        await session.execute(select(User).where(User.id == user_id, User.company_id == company_id))
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("User", user_id)
    return UserSummary(
        id=user.id, full_name=user.full_name, permissions_version=user.permissions_version
    )


async def bump_permissions_version(session: AsyncSession, *, user_id: UUID) -> None:
    """Invalidate the user's cached AccessContext.

    Called whenever a grant is created or revoked, so the change takes effect
    on their very next request rather than waiting for a TTL.
    """
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
    user.permissions_version += 1

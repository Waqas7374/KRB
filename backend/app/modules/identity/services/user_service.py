"""User administration: invite, edit, deactivate, reset.

Creating a user does not send them credentials in the response — an invited
user has no password at all until they complete an invite flow (§35: never
expose sensitive credentials, never store or transmit a plain-text password).
For Phase 1 the invite token is logged the same way the password-reset token
is (see `identity.services.auth`); the email template lands with the
notification worker.

Role-grant creation and revocation live in `access.services.grants` instead
of here: a `UserRoleGrant` is fundamentally an access-module row, and whether
a grant is legal is access-module business. See that module's docstring.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import BusinessRuleError
from app.core.types import uuid7
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.services.writer import record as record_audit
from app.modules.identity.domain.enums import SessionRevocationReason, UserStatus
from app.modules.identity.models import User
from app.modules.identity.services.auth import (
    create_password_reset,
    logout_everywhere,
    normalise_phone,
)
from app.platform import outbox


def user_repository(session: AsyncSession) -> ScopedRepository[User]:
    return ScopedRepository(
        session,
        User,
        entity_name="User",
        sortable={"full_name", "email", "status", "created_at", "last_login_at"},
        searchable=("full_name", "email", "phone"),
        default_sort="full_name",
    )


async def invite_user(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    email: str | None,
    phone: str | None,
    full_name: str,
    default_project_id: UUID | None,
    default_site_id: UUID | None,
) -> tuple[User, str]:
    """Create an invited user and return (user, reset_token).

    The token is single-use and expires the same way a password-reset token
    does; the invited user sets their own password through the same
    `/auth/password/reset` endpoint, so there is exactly one code path for
    "a person chooses their password" rather than two that could drift apart.
    """
    repo = user_repository(session)

    if email:
        await repo.assert_code_available(ctx.company_id, "email", email)
    normalised_phone = normalise_phone(phone) if phone else None
    if normalised_phone:
        await repo.assert_code_available(ctx.company_id, "phone", normalised_phone)

    user = User(
        id=uuid7(),
        company_id=ctx.company_id,
        email=email,
        phone=normalised_phone,
        full_name=full_name,
        status=UserStatus.INVITED.value,
        must_change_password=True,
        default_project_id=default_project_id,
        default_site_id=default_site_id,
    )
    session.add(user)
    await session.flush()

    token = await create_password_reset(session, user=user, purpose="invite")

    await record_audit(
        session,
        action=AuditAction.CREATE,
        entity_type="User",
        entity_id=user.id,
        entity_label=user.full_name,
        company_id=ctx.company_id,
        summary=f"User invited: {user.full_name}",
    )
    await outbox.emit(
        session,
        outbox.DomainEvent(
            event_type="user.invited",
            aggregate_type="User",
            aggregate_id=user.id,
            payload={"full_name": user.full_name, "email": email, "phone": normalised_phone},
            company_id=ctx.company_id,
        ),
    )
    return user, token


async def update_user(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    user_id: UUID,
    changes: dict[str, Any],
    expected_version: int | None = None,
) -> User:
    repo = user_repository(session)
    user = await repo.get_for_update(ctx, "users.view", user_id)

    if changes.get("phone"):
        changes["phone"] = normalise_phone(changes["phone"])

    repo.apply_update(user, changes, expected_version=expected_version)
    return user


async def deactivate_user(
    session: AsyncSession, ctx: AccessContext, *, user_id: UUID, reason: str | None
) -> User:
    if user_id == ctx.user_id:
        raise BusinessRuleError("cannot_deactivate_self", "You cannot deactivate your own account.")

    repo = user_repository(session)
    user = await repo.get_for_update(ctx, "users.view", user_id)

    previous = user.status
    user.status = UserStatus.DEACTIVATED.value
    user.version += 1
    await logout_everywhere(
        session, user_id=user.id, reason=SessionRevocationReason.USER_DEACTIVATED
    )

    await record_audit(
        session,
        action=AuditAction.UPDATE,
        entity_type="User",
        entity_id=user.id,
        entity_label=user.full_name,
        company_id=ctx.company_id,
        summary=f"User deactivated ({previous} -> DEACTIVATED)" + (f": {reason}" if reason else ""),
        old_values={"status": previous},
        new_values={"status": UserStatus.DEACTIVATED.value},
    )
    return user


async def admin_reset_password(
    session: AsyncSession, ctx: AccessContext, *, user_id: UUID
) -> tuple[User, str]:
    """An administrator forces a password reset for someone else.

    Returns the reset token; delivery is the caller's job (email in
    production, logged in development — see `identity.api.routes`).
    """
    repo = user_repository(session)
    user = await repo.get(ctx, "users.view", user_id)

    token = await create_password_reset(session, user=user, purpose="admin_reset")
    user.must_change_password = True
    if user.status == UserStatus.ACTIVE.value:
        user.status = UserStatus.PASSWORD_RESET_REQUIRED.value

    await record_audit(
        session,
        action=AuditAction.PASSWORD_RESET,
        entity_type="User",
        entity_id=user.id,
        entity_label=user.full_name,
        company_id=ctx.company_id,
        summary=f"Password reset forced by an administrator for {user.full_name}",
    )
    return user, token

"""Authentication: login, refresh rotation, logout, password reset.

Design points that matter:

* **One identifier, two spellings.** The web signs in with email, the site app
  with a phone number (§13). Both resolve to the same user row.
* **Refresh-token rotation with reuse detection.** Each refresh issues a new
  token and revokes the old one. Presenting an already-rotated token means it
  leaked, so the entire chain is revoked.
* **Constant-ish work on failure.** An unknown identifier still runs a password
  verification, so response timing does not disclose whether an account exists.
* **Lockout is per account and exponential**, and a locked account is told it is
  locked rather than being told the password is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import current_context
from app.core.db import SessionFactory
from app.core.errors import (
    AccountLockedError,
    AuthenticationError,
    InvalidCredentialsError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    lockout_until,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.core.types import utcnow, uuid7
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.services.writer import record as record_audit
from app.modules.identity.domain.enums import (
    DevicePlatform,
    LoginIdentifier,
    SessionRevocationReason,
    UserStatus,
)
from app.modules.identity.models import PasswordResetToken, User, UserDevice, UserSession

log = get_logger("auth")


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    session_id: UUID
    user: User


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    device_uid: str | None = None
    platform: str | None = None
    model: str | None = None
    os_version: str | None = None
    app_version: str | None = None
    push_token: str | None = None
    is_rooted: bool = False


# -----------------------------------------------------------------------------
# Login
# -----------------------------------------------------------------------------


def _looks_like_phone(identifier: str) -> bool:
    stripped = identifier.replace(" ", "").replace("-", "")
    return "@" not in identifier and (
        stripped.startswith("+") or stripped.isdigit() or stripped.startswith("0")
    )


def normalise_phone(phone: str) -> str:
    """Normalise a Pakistani mobile number to E.164.

    Site staff type `0300-1234567`; head office types `+92 300 1234567`. Both
    must find the same user, so the stored form is canonical.
    """
    digits = "".join(c for c in phone if c.isdigit() or c == "+")
    if digits.startswith("+"):
        return digits
    if digits.startswith("00"):
        return f"+{digits[2:]}"
    if digits.startswith("0"):
        return f"+92{digits[1:]}"
    if digits.startswith("92"):
        return f"+{digits}"
    return f"+92{digits}"


async def find_user_by_identifier(
    session: AsyncSession, identifier: str, company_id: UUID | None = None
) -> tuple[User | None, LoginIdentifier]:
    identifier = identifier.strip()
    if _looks_like_phone(identifier):
        kind = LoginIdentifier.PHONE
        stmt = select(User).where(User.phone == normalise_phone(identifier))
    else:
        kind = LoginIdentifier.EMAIL
        stmt = select(User).where(User.email == identifier)

    if company_id is not None:
        stmt = stmt.where(User.company_id == company_id)

    return (await session.execute(stmt)).scalar_one_or_none(), kind


async def login(
    session: AsyncSession,
    *,
    identifier: str,
    password: str,
    device: DeviceInfo | None = None,
    company_id: UUID | None = None,
) -> TokenPair:
    ctx = current_context()
    user, identifier_kind = await find_user_by_identifier(session, identifier, company_id)

    # Verify a password even when the user is unknown, so an attacker cannot
    # enumerate accounts by comparing response times.
    password_ok = verify_password(password, user.password_hash if user else None)

    if user is None:
        await record_audit(
            session,
            action=AuditAction.LOGIN_FAILED,
            entity_type="User",
            entity_label=identifier[:200],
            summary=f"Sign-in attempt for unknown {identifier_kind.value.lower()}",
        )
        raise InvalidCredentialsError("Incorrect credentials")

    if user.locked_until is not None and user.locked_until > utcnow():
        remaining = int((user.locked_until - utcnow()).total_seconds() // 60) + 1
        await record_audit(
            session,
            action=AuditAction.LOGIN_FAILED,
            entity_type="User",
            entity_id=user.id,
            entity_label=user.full_name,
            company_id=user.company_id,
            summary="Sign-in attempt on a locked account",
        )
        raise AccountLockedError(f"Account is locked. Try again in about {remaining} minutes.")

    if not password_ok:
        await _register_failed_attempt(session, user)
        raise InvalidCredentialsError("Incorrect credentials")

    if user.status == UserStatus.INVITED.value:
        raise AuthenticationError(
            "This invitation has not been accepted yet. Use the link in your invitation email."
        )
    if not user.can_log_in:
        raise AuthenticationError(f"Account is {user.status.replace('_', ' ').lower()}")

    # Upgrade the stored hash if the cost parameters have since been raised.
    if user.password_hash and needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    user.last_login_ip = ctx.ip

    device_row = await _upsert_device(session, user, device)
    pair = await _issue_tokens(session, user, device_row)

    await record_audit(
        session,
        action=AuditAction.LOGIN,
        entity_type="User",
        entity_id=user.id,
        entity_label=user.full_name,
        company_id=user.company_id,
        summary=f"Signed in by {identifier_kind.value.lower()}"
        + (f" from {device.platform}" if device and device.platform else ""),
        actor_user_id=user.id,
    )
    log.info("auth.login", user_id=str(user.id), via=identifier_kind.value)
    return pair


async def _register_failed_attempt(session: AsyncSession, user: User) -> None:
    user.failed_login_count += 1
    locked = lockout_until(user.failed_login_count)
    if locked is not None:
        user.locked_until = locked

    await record_audit(
        session,
        action=AuditAction.ACCOUNT_LOCKED if locked else AuditAction.LOGIN_FAILED,
        entity_type="User",
        entity_id=user.id,
        entity_label=user.full_name,
        company_id=user.company_id,
        summary=(
            f"Account locked after {user.failed_login_count} failed attempts"
            if locked
            else f"Failed sign-in attempt ({user.failed_login_count})"
        ),
        actor_user_id=user.id,
    )
    log.warning(
        "auth.login_failed",
        user_id=str(user.id),
        attempts=user.failed_login_count,
        locked=locked is not None,
    )


# -----------------------------------------------------------------------------
# Tokens
# -----------------------------------------------------------------------------


async def _issue_tokens(
    session: AsyncSession,
    user: User,
    device: UserDevice | None,
    *,
    rotated_from: UserSession | None = None,
) -> TokenPair:
    ctx = current_context()
    now = utcnow()

    refresh_token = generate_opaque_token()
    user_session = UserSession(
        id=uuid7(),
        user_id=user.id,
        refresh_token_hash=hash_token(refresh_token),
        device_id=device.id if device else None,
        ip=ctx.ip,
        user_agent=ctx.user_agent,
        issued_at=now,
        expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
        rotated_from_id=rotated_from.id if rotated_from else None,
    )
    session.add(user_session)

    access_token, access_expires = create_access_token(
        user_id=user.id,
        company_id=user.company_id,
        session_id=user_session.id,
        permissions_version=user.permissions_version,
    )

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_at=access_expires,
        refresh_expires_at=user_session.expires_at,
        session_id=user_session.id,
        user=user,
    )


async def refresh(session: AsyncSession, *, refresh_token: str) -> TokenPair:
    """Exchange a refresh token for a new pair, rotating the old one.

    Reuse of an already-rotated token is treated as theft: the whole chain is
    revoked, because either the legitimate holder or an attacker has a copy and
    there is no way to tell which.
    """
    token_hash = hash_token(refresh_token)
    user_session = (
        await session.execute(
            select(UserSession).where(UserSession.refresh_token_hash == token_hash)
        )
    ).scalar_one_or_none()

    if user_session is None:
        raise AuthenticationError("Invalid refresh token")

    if user_session.revoked_at is not None:
        # Reuse of an already-rotated token: either the legitimate holder or an
        # attacker has a copy, and there is no way to tell which. Revoke
        # everything.
        #
        # This runs in its own committed transaction, because the request is
        # about to fail and the caller's transaction will be rolled back. A
        # security response that gets discarded along with the failed request
        # is not a security response.
        await _revoke_all_sessions_committed(
            user_id=user_session.user_id, triggered_by_session=user_session.id
        )
        log.warning(
            "auth.refresh_reuse_detected",
            session_id=str(user_session.id),
            user_id=str(user_session.user_id),
        )
        raise AuthenticationError("This session has been revoked. Sign in again.")

    if user_session.expires_at <= utcnow():
        user_session.revoked_at = utcnow()
        user_session.revoked_reason = SessionRevocationReason.EXPIRED.value
        raise AuthenticationError("Session expired. Sign in again.")

    user = (
        await session.execute(select(User).where(User.id == user_session.user_id))
    ).scalar_one_or_none()
    if user is None or not user.can_log_in:
        raise AuthenticationError("Account is no longer active")

    device = None
    if user_session.device_id is not None:
        device = (
            await session.execute(select(UserDevice).where(UserDevice.id == user_session.device_id))
        ).scalar_one_or_none()
        if device is not None and device.revoked_at is not None:
            raise AuthenticationError("This device's access has been revoked")

    user_session.revoked_at = utcnow()
    user_session.revoked_reason = SessionRevocationReason.ROTATED.value
    user_session.last_used_at = utcnow()

    return await _issue_tokens(session, user, device, rotated_from=user_session)


async def _revoke_all_sessions_committed(*, user_id: UUID, triggered_by_session: UUID) -> None:
    """Revoke every live session for a user, in a transaction of its own.

    Walking the `rotated_from` chain precisely would still leave sibling
    branches live, and a leaked token is reason to distrust the whole account's
    sessions rather than one lineage.

    Opens its own session so the revocation commits even though the caller is
    about to raise. `SessionFactory` is referenced through the module namespace
    on purpose, so a test can point this side effect at its own transaction.
    """
    async with SessionFactory() as own_session:
        result: CursorResult[Any] = await own_session.execute(  # type: ignore[assignment]
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(
                revoked_at=utcnow(),
                revoked_reason=SessionRevocationReason.REUSE_DETECTED.value,
            )
        )
        await record_audit(
            own_session,
            action=AuditAction.LOGOUT,
            entity_type="UserSession",
            entity_id=triggered_by_session,
            summary=(
                f"Refresh-token reuse detected; {result.rowcount or 0} active session(s) revoked"
            ),
            actor_user_id=user_id,
        )
        await own_session.commit()


async def logout(session: AsyncSession, *, session_id: UUID, user_id: UUID) -> None:
    await session.execute(
        update(UserSession)
        .where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow(), revoked_reason=SessionRevocationReason.LOGOUT.value)
    )
    await record_audit(
        session,
        action=AuditAction.LOGOUT,
        entity_type="UserSession",
        entity_id=session_id,
        summary="Signed out",
        actor_user_id=user_id,
    )


async def logout_everywhere(
    session: AsyncSession, *, user_id: UUID, reason: SessionRevocationReason
) -> int:
    result: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason.value)
    )
    return int(result.rowcount or 0)


# -----------------------------------------------------------------------------
# Devices
# -----------------------------------------------------------------------------


async def _upsert_device(
    session: AsyncSession, user: User, device: DeviceInfo | None
) -> UserDevice | None:
    if device is None or not device.device_uid:
        return None

    existing = (
        await session.execute(
            select(UserDevice).where(
                UserDevice.user_id == user.id, UserDevice.device_uid == device.device_uid
            )
        )
    ).scalar_one_or_none()

    now = utcnow()
    if existing is not None:
        if existing.revoked_at is not None:
            raise AuthenticationError("This device's access has been revoked")
        existing.last_seen_at = now
        existing.app_version = device.app_version or existing.app_version
        existing.os_version = device.os_version or existing.os_version
        existing.push_token = device.push_token or existing.push_token
        existing.is_rooted = device.is_rooted
        return existing

    row = UserDevice(
        id=uuid7(),
        user_id=user.id,
        device_uid=device.device_uid,
        platform=device.platform or DevicePlatform.ANDROID.value,
        model=device.model,
        os_version=device.os_version,
        app_version=device.app_version,
        push_token=device.push_token,
        first_seen_at=now,
        last_seen_at=now,
        is_rooted=device.is_rooted,
    )
    session.add(row)
    await record_audit(
        session,
        action=AuditAction.DEVICE_REGISTERED,
        entity_type="UserDevice",
        entity_id=row.id,
        entity_label=device.model or device.device_uid,
        company_id=user.company_id,
        summary=f"New {row.platform} device registered",
        actor_user_id=user.id,
    )
    return row


# -----------------------------------------------------------------------------
# Passwords
# -----------------------------------------------------------------------------


async def change_password(
    session: AsyncSession, *, user: User, current_password: str, new_password: str
) -> None:
    if not verify_password(current_password, user.password_hash):
        raise InvalidCredentialsError("Current password is incorrect")

    _assert_password_acceptable(new_password, user)

    user.password_hash = hash_password(new_password)
    user.password_changed_at = utcnow()
    user.must_change_password = False
    if user.status == UserStatus.PASSWORD_RESET_REQUIRED.value:
        user.status = UserStatus.ACTIVE.value

    # Every other session is invalidated: changing a password is how someone
    # responds to a suspected compromise, and it has to mean something.
    await logout_everywhere(
        session, user_id=user.id, reason=SessionRevocationReason.PASSWORD_CHANGED
    )
    await record_audit(
        session,
        action=AuditAction.PASSWORD_CHANGE,
        entity_type="User",
        entity_id=user.id,
        entity_label=user.full_name,
        company_id=user.company_id,
        summary="Password changed; all other sessions signed out",
        actor_user_id=user.id,
    )


def _assert_password_acceptable(password: str, user: User) -> None:
    problems = validate_password_strength(password, identifier=user.email or user.phone)
    if problems:
        raise ValidationError(
            "Password does not meet the policy",
            errors=[
                {"field": "new_password", "code": "weak_password", "message": problem}
                for problem in problems
            ],
        )


async def create_password_reset(session: AsyncSession, *, user: User) -> str:
    """Issue a single-use reset token. Returns the clear token, stored hashed."""
    token = generate_opaque_token(32)
    session.add(
        PasswordResetToken(
            id=uuid7(),
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=utcnow() + timedelta(minutes=settings.password_reset_ttl_minutes),
            requested_ip=current_context().ip,
        )
    )
    return token


async def complete_password_reset(session: AsyncSession, *, token: str, new_password: str) -> User:
    row = (
        await session.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(token))
        )
    ).scalar_one_or_none()

    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        raise AuthenticationError("This reset link is invalid or has expired")

    user = (await session.execute(select(User).where(User.id == row.user_id))).scalar_one()
    _assert_password_acceptable(new_password, user)

    row.used_at = utcnow()
    user.password_hash = hash_password(new_password)
    user.password_changed_at = utcnow()
    user.must_change_password = False
    user.failed_login_count = 0
    user.locked_until = None
    if user.status in {UserStatus.INVITED.value, UserStatus.PASSWORD_RESET_REQUIRED.value}:
        user.status = UserStatus.ACTIVE.value

    await logout_everywhere(
        session, user_id=user.id, reason=SessionRevocationReason.PASSWORD_CHANGED
    )
    await record_audit(
        session,
        action=AuditAction.PASSWORD_RESET,
        entity_type="User",
        entity_id=user.id,
        entity_label=user.full_name,
        company_id=user.company_id,
        summary="Password reset completed; all sessions signed out",
        actor_user_id=user.id,
    )
    return user

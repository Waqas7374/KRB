"""Password hashing and token handling.

Argon2id for passwords (memory-hard, the current OWASP recommendation).
Short-lived JWT access tokens paired with opaque, rotating refresh tokens held
server-side, so a stolen refresh token can be detected and the whole chain
revoked.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings
from app.core.errors import TokenExpiredError
from app.core.types import utcnow, uuid7_str

TokenType = Literal["access", "refresh", "reset", "invite"]

_hasher = PasswordHasher(
    time_cost=settings.argon2_time_cost,
    memory_cost=settings.argon2_memory_cost,
    parallelism=settings.argon2_parallelism,
)

# A pre-computed hash used to keep failed logins as slow as successful ones, so
# response timing does not reveal whether an account exists.
_DUMMY_HASH = _hasher.hash("timing-attack-mitigation")


# -----------------------------------------------------------------------------
# Passwords
# -----------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Verify a password. Always does the work, even when the user is unknown."""
    if not password_hash:
        # Burn the same CPU as a real verification so an unknown user and a
        # wrong password are indistinguishable by response time.
        with suppress(VerifyMismatchError, VerificationError, InvalidHashError):
            _hasher.verify(_DUMMY_HASH, password)
        return False
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    """True when the hash was made with weaker parameters than current policy."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# -----------------------------------------------------------------------------
# Opaque tokens (refresh, password reset)
# -----------------------------------------------------------------------------


def generate_opaque_token(nbytes: int = 48) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """HMAC-SHA256 of an opaque token.

    Stored instead of the token itself, so a database leak does not hand an
    attacker usable refresh tokens. HMAC rather than a bare digest so the
    application secret is required to precompute a lookup table.
    """
    return hmac.new(settings.secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


def tokens_match(token: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), stored_hash)


# -----------------------------------------------------------------------------
# JWT access tokens
# -----------------------------------------------------------------------------


def create_access_token(
    *,
    user_id: UUID,
    company_id: UUID | None,
    session_id: UUID,
    permissions_version: int,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, datetime]:
    """Return (token, expires_at).

    Permissions are deliberately NOT embedded. They are resolved per request
    from the cached AccessContext, so revoking a role takes effect immediately
    rather than after the access token expires. `permissions_version` lets the
    server detect a token issued before a grant change.
    """
    now = utcnow()
    expires_at = now + timedelta(minutes=settings.access_token_ttl_minutes)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "sid": str(session_id),
        "cid": str(company_id) if company_id else None,
        "pv": permissions_version,
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid7_str(),
        "iss": settings.app_name,
    }
    if extra_claims:
        payload.update(extra_claims)
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, expires_at


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.app_name,
            options={"require": ["exp", "sub", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError("Access token has expired") from exc
    except jwt.InvalidTokenError as exc:
        from app.core.errors import AuthenticationError

        raise AuthenticationError("Invalid access token") from exc

    if payload.get("typ") != "access":
        from app.core.errors import AuthenticationError

        raise AuthenticationError("Wrong token type")
    return payload


# -----------------------------------------------------------------------------
# Lockout
# -----------------------------------------------------------------------------


def lockout_until(failed_count: int) -> datetime | None:
    """Exponential backoff once the failure threshold is crossed.

    5 failures -> 15 min, 6 -> 30, 7 -> 60, capped at 24 h.
    """
    if failed_count < settings.max_failed_logins:
        return None
    over = failed_count - settings.max_failed_logins
    minutes = min(settings.lockout_minutes * (2**over), 24 * 60)
    return utcnow() + timedelta(minutes=minutes)


# -----------------------------------------------------------------------------
# Password policy
# -----------------------------------------------------------------------------

_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "12345678",
        "123456789",
        "qwerty123",
        "admin123",
        "welcome1",
        "changeme",
        "letmein1",
        "iloveyou",
    }
)


def validate_password_strength(password: str, *, identifier: str | None = None) -> list[str]:
    """Return a list of policy violations; empty means acceptable.

    Length-first, in line with NIST 800-63B: no forced character-class rules
    that push people towards `Password1!`, but a floor of 12 characters and a
    check against the obvious choices.
    """
    problems: list[str] = []
    if len(password) < 12:
        problems.append("must be at least 12 characters")
    if len(password) > 128:
        problems.append("must be at most 128 characters")
    if password.lower() in _COMMON_PASSWORDS:
        problems.append("is too common")
    if identifier and identifier.lower().split("@")[0] in password.lower():
        problems.append("must not contain your username or email")
    if password.strip() != password:
        problems.append("must not begin or end with whitespace")
    return problems

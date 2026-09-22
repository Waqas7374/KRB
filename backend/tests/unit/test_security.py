"""Password hashing, token handling and lockout policy."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.errors import AuthenticationError, TokenExpiredError
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    lockout_until,
    tokens_match,
    validate_password_strength,
    verify_password,
)
from app.core.types import utcnow


class TestPasswordHashing:
    def test_hash_is_argon2id(self) -> None:
        assert hash_password("correct horse battery staple").startswith("$argon2id$")

    def test_hash_is_salted(self) -> None:
        password = "correct horse battery staple"
        assert hash_password(password) != hash_password(password)

    def test_verify_accepts_the_right_password(self) -> None:
        assert verify_password("s3cret-passphrase", hash_password("s3cret-passphrase"))

    def test_verify_rejects_the_wrong_password(self) -> None:
        assert not verify_password("wrong", hash_password("s3cret-passphrase"))

    def test_verify_handles_a_missing_hash(self) -> None:
        """An unknown user must not short-circuit — that leaks existence via timing."""
        assert verify_password("anything", None) is False

    def test_verify_handles_a_corrupt_hash(self) -> None:
        assert verify_password("anything", "not-a-hash") is False


class TestOpaqueTokens:
    def test_tokens_are_unpredictable(self) -> None:
        assert len({generate_opaque_token() for _ in range(500)}) == 500

    def test_stored_hash_differs_from_the_token(self) -> None:
        token = generate_opaque_token()
        assert hash_token(token) != token

    def test_match_is_true_only_for_the_original(self) -> None:
        token = generate_opaque_token()
        stored = hash_token(token)
        assert tokens_match(token, stored)
        assert not tokens_match(generate_opaque_token(), stored)


class TestAccessTokens:
    def test_round_trip_carries_the_claims(self) -> None:
        user_id, company_id, session_id = uuid4(), uuid4(), uuid4()
        token, expires_at = create_access_token(
            user_id=user_id, company_id=company_id, session_id=session_id, permissions_version=3
        )
        payload = decode_access_token(token)

        assert payload["sub"] == str(user_id)
        assert payload["cid"] == str(company_id)
        assert payload["sid"] == str(session_id)
        assert payload["pv"] == 3
        assert payload["typ"] == "access"
        assert expires_at > utcnow()

    def test_permissions_are_not_embedded(self) -> None:
        """Revoking a role must take effect at once, not when the token expires."""
        token, _ = create_access_token(
            user_id=uuid4(), company_id=uuid4(), session_id=uuid4(), permissions_version=1
        )
        payload = decode_access_token(token)
        assert "permissions" not in payload
        assert "roles" not in payload

    def test_tampered_token_is_rejected(self) -> None:
        token, _ = create_access_token(
            user_id=uuid4(), company_id=None, session_id=uuid4(), permissions_version=1
        )
        head, body, signature = token.split(".")
        with pytest.raises(AuthenticationError):
            decode_access_token(f"{head}.{body}.{signature[:-2]}xx")

    def test_expired_token_raises_expired_not_invalid(self) -> None:
        import jwt

        payload = {
            "sub": str(uuid4()),
            "typ": "access",
            "iss": settings.app_name,
            "iat": int((utcnow() - timedelta(hours=2)).timestamp()),
            "exp": int((utcnow() - timedelta(hours=1)).timestamp()),
        }
        expired = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
        with pytest.raises(TokenExpiredError):
            decode_access_token(expired)

    def test_refresh_token_is_not_accepted_as_access(self) -> None:
        import jwt

        payload = {
            "sub": str(uuid4()),
            "typ": "refresh",
            "iss": settings.app_name,
            "exp": int((utcnow() + timedelta(hours=1)).timestamp()),
        }
        token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
        with pytest.raises(AuthenticationError):
            decode_access_token(token)


class TestLockout:
    def test_no_lockout_below_the_threshold(self) -> None:
        assert lockout_until(settings.max_failed_logins - 1) is None

    def test_lockout_at_the_threshold(self) -> None:
        assert lockout_until(settings.max_failed_logins) is not None

    def test_backoff_is_exponential_and_capped(self) -> None:
        first = lockout_until(settings.max_failed_logins)
        second = lockout_until(settings.max_failed_logins + 1)
        assert first is not None and second is not None
        assert second > first

        capped = lockout_until(settings.max_failed_logins + 50)
        assert capped is not None
        assert capped <= utcnow() + timedelta(hours=24, minutes=1)


class TestPasswordPolicy:
    def test_accepts_a_long_passphrase(self) -> None:
        assert validate_password_strength("correct horse battery staple") == []

    def test_rejects_short_passwords(self) -> None:
        assert "must be at least 12 characters" in validate_password_strength("Short1!")

    def test_rejects_common_passwords(self) -> None:
        problems = validate_password_strength("password1")
        assert "is too common" in problems

    def test_rejects_password_containing_the_email(self) -> None:
        problems = validate_password_strength(
            "ahmed.raza-is-here", identifier="ahmed.raza@krb.example"
        )
        assert "must not contain your username or email" in problems

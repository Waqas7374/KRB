"""Domain exception hierarchy and its mapping to RFC 9457 problem responses.

Services raise these; the API layer never constructs HTTP errors by hand. That
keeps the wire contract in one place and makes the same exception usable from a
Celery task, where there is no HTTP response to build.
"""

from __future__ import annotations

from typing import Any

ERROR_BASE_URI = "https://errors.krb-erp/"


class AppError(Exception):
    """Base for every error this application raises deliberately."""

    status_code: int = 500
    error_type: str = "internal"
    title: str = "Internal server error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        errors: list[dict[str, Any]] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.detail = detail or self.title
        self.errors = errors or []
        self.extra = extra or {}
        super().__init__(self.detail)

    def to_problem(self, instance: str, request_id: str) -> dict[str, Any]:
        problem: dict[str, Any] = {
            "type": f"{ERROR_BASE_URI}{self.error_type}",
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
            "instance": instance,
            "request_id": request_id,
        }
        if self.errors:
            problem["errors"] = self.errors
        problem.update(self.extra)
        return problem


# --- 4xx ---------------------------------------------------------------------


class ValidationError(AppError):
    status_code = 422
    error_type = "validation"
    title = "Validation failed"


class BadRequestError(AppError):
    status_code = 400
    error_type = "bad-request"
    title = "Bad request"


class AuthenticationError(AppError):
    status_code = 401
    error_type = "authentication"
    title = "Authentication required"


class InvalidCredentialsError(AuthenticationError):
    error_type = "invalid-credentials"
    title = "Invalid credentials"


class TokenExpiredError(AuthenticationError):
    error_type = "token-expired"
    title = "Token expired"


class AccountLockedError(AuthenticationError):
    status_code = 423
    error_type = "account-locked"
    title = "Account temporarily locked"


class PermissionDeniedError(AppError):
    status_code = 403
    error_type = "permission-denied"
    title = "Permission denied"

    def __init__(self, permission: str | None = None, detail: str | None = None) -> None:
        super().__init__(
            detail or (f"Missing permission: {permission}" if permission else "Permission denied"),
            extra={"required_permission": permission} if permission else None,
        )


class NotFoundError(AppError):
    """Also raised when a record exists but lies outside the caller's scope.

    Returning 403 in that case would confirm the record's existence and leak
    numbering sequences and vendor lists. See docs/03-rbac.md §5.
    """

    status_code = 404
    error_type = "not-found"
    title = "Not found"

    def __init__(self, entity: str = "Resource", identifier: object | None = None) -> None:
        detail = f"{entity} not found" if identifier is None else f"{entity} {identifier} not found"
        super().__init__(detail, extra={"entity": entity})


class ConflictError(AppError):
    status_code = 409
    error_type = "conflict"
    title = "Conflict"


class DuplicateError(ConflictError):
    error_type = "duplicate"
    title = "Already exists"

    def __init__(self, entity: str, field: str, value: object) -> None:
        super().__init__(
            f"{entity} with {field} '{value}' already exists",
            errors=[{"field": field, "code": "duplicate", "message": "already in use"}],
        )


class VersionConflictError(ConflictError):
    error_type = "version-conflict"
    title = "Record was modified by someone else"


class IdempotencyConflictError(ConflictError):
    error_type = "idempotency-conflict"
    title = "Idempotency key reused with a different payload"


class StateTransitionError(AppError):
    status_code = 409
    error_type = "invalid-state"
    title = "Invalid state transition"

    def __init__(self, entity: str, current: str, attempted: str) -> None:
        super().__init__(
            f"{entity} cannot move from {current} to {attempted}",
            extra={"current_status": current, "attempted_status": attempted},
        )


class BusinessRuleError(AppError):
    """A rule the business defined was broken — distinct from a schema failure."""

    status_code = 422
    error_type = "business-rule"
    title = "Business rule violation"

    def __init__(self, rule: str, detail: str) -> None:
        super().__init__(detail, extra={"rule": rule})


class PeriodClosedError(AppError):
    status_code = 423
    error_type = "period-closed"
    title = "Accounting period is closed"


class ConversionNotConfiguredError(AppError):
    """No unit conversion is configured for the requested pair.

    Never falls back to a guessed factor — see docs/05-rules-rates-and-units.md.
    """

    status_code = 422
    error_type = "conversion-not-configured"
    title = "Unit conversion not configured"

    def __init__(self, from_unit: str, to_unit: str, scope: str = "") -> None:
        super().__init__(
            f"No conversion from {from_unit} to {to_unit}"
            + (f" for {scope}" if scope else "")
            + ". An administrator must configure it.",
            extra={"from_unit": from_unit, "to_unit": to_unit},
        )


class RateLimitedError(AppError):
    status_code = 429
    error_type = "rate-limited"
    title = "Too many requests"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            f"Too many requests. Retry in {retry_after_seconds}s.",
            extra={"retry_after": retry_after_seconds},
        )
        self.retry_after_seconds = retry_after_seconds


class ServiceUnavailableError(AppError):
    status_code = 503
    error_type = "service-unavailable"
    title = "Service unavailable"

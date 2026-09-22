"""Identity request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.identity.domain.enums import DevicePlatform


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


# -----------------------------------------------------------------------------
# Login
# -----------------------------------------------------------------------------


class DeviceInfoIn(ApiModel):
    """Sent by the mobile app so head office can see and revoke devices."""

    device_uid: Annotated[str, Field(max_length=128)]
    platform: DevicePlatform = DevicePlatform.ANDROID
    model: Annotated[str | None, Field(max_length=120)] = None
    os_version: Annotated[str | None, Field(max_length=40)] = None
    app_version: Annotated[str | None, Field(max_length=40)] = None
    push_token: Annotated[str | None, Field(max_length=400)] = None
    is_rooted: bool = False


class LoginRequest(ApiModel):
    # One field for both spellings: the web sends an email, the site app a
    # phone number (§13). The server works out which it is.
    identifier: Annotated[str, Field(min_length=3, max_length=200, examples=["0300-1234567"])]
    password: Annotated[str, Field(min_length=1, max_length=128)]
    device: DeviceInfoIn | None = None

    @field_validator("identifier")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class TokenResponse(ApiModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"  # noqa: S105 — scheme name, not a secret
    expires_in: int = Field(description="Access-token lifetime in seconds")
    refresh_expires_at: datetime
    user: UserProfile


class RefreshRequest(ApiModel):
    refresh_token: Annotated[str, Field(min_length=10, max_length=400)]


# -----------------------------------------------------------------------------
# Profile
# -----------------------------------------------------------------------------


class RoleGrantSummary(ApiModel):
    role_code: str
    role_name: str
    scope_type: str
    scope_id: UUID | None
    scope_label: str | None = None


class UserProfile(ApiModel):
    id: UUID
    company_id: UUID
    full_name: str
    display_name: str | None
    email: str | None
    phone: str | None
    status: str
    must_change_password: bool
    default_project_id: UUID | None
    default_site_id: UUID | None
    locale: str
    timezone: str | None
    last_login_at: datetime | None


class MeResponse(ApiModel):
    """Everything the client needs to render the correct application.

    The permission list and scopes are sent so the UI can hide what the user
    cannot do — a convenience, never a control. Every check is re-run
    server-side.
    """

    user: UserProfile
    company: CompanySummary
    roles: list[RoleGrantSummary]
    permissions: list[str]
    is_superuser: bool
    is_read_only: bool
    # permission code -> the projects/sites it applies to, empty meaning
    # company-wide.
    scopes: dict[str, ScopeSummary]


class ScopeSummary(ApiModel):
    is_global: bool = False
    is_company_wide: bool = False
    project_ids: list[UUID] = Field(default_factory=list)
    site_ids: list[UUID] = Field(default_factory=list)
    department_ids: list[UUID] = Field(default_factory=list)


class CompanySummary(ApiModel):
    id: UUID
    code: str
    name: str
    base_currency: str
    timezone: str
    locale: str
    fiscal_year_start_month: int


# -----------------------------------------------------------------------------
# Passwords
# -----------------------------------------------------------------------------


class ChangePasswordRequest(ApiModel):
    current_password: Annotated[str, Field(min_length=1, max_length=128)]
    new_password: Annotated[str, Field(min_length=12, max_length=128)]


class ForgotPasswordRequest(ApiModel):
    identifier: Annotated[str, Field(min_length=3, max_length=200)]


class ResetPasswordRequest(ApiModel):
    token: Annotated[str, Field(min_length=10, max_length=400)]
    new_password: Annotated[str, Field(min_length=12, max_length=128)]


class MessageResponse(ApiModel):
    message: str
    detail: str | None = None


# -----------------------------------------------------------------------------
# Sessions & devices
# -----------------------------------------------------------------------------


class SessionSummary(ApiModel):
    id: UUID
    ip: str | None
    user_agent: str | None
    device_id: UUID | None
    issued_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    is_current: bool = False


class DeviceSummary(ApiModel):
    id: UUID
    device_uid: str
    platform: str
    model: str | None
    app_version: str | None
    last_seen_at: datetime | None
    last_sync_at: datetime | None
    is_trusted: bool
    is_rooted: bool
    revoked_at: datetime | None


def _rebuild() -> None:
    """Resolve the forward references used above."""
    for model in (TokenResponse, MeResponse):
        model.model_rebuild()


_rebuild()

__all__: list[str] = [
    "ChangePasswordRequest",
    "CompanySummary",
    "DeviceInfoIn",
    "DeviceSummary",
    "ForgotPasswordRequest",
    "LoginRequest",
    "MeResponse",
    "MessageResponse",
    "RefreshRequest",
    "ResetPasswordRequest",
    "RoleGrantSummary",
    "ScopeSummary",
    "SessionSummary",
    "TokenResponse",
    "UserProfile",
]

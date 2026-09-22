"""User-administration schemas.

Kept separate from `schemas.py` (the auth-flow request/response shapes) so the
two concerns — "how someone signs in" and "how an administrator manages
accounts" — stay easy to tell apart.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.access.domain.enums import ScopeType


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


# -----------------------------------------------------------------------------
# Users
# -----------------------------------------------------------------------------


class UserInvite(ApiModel):
    email: Annotated[str | None, Field(max_length=160)] = None
    phone: Annotated[str | None, Field(max_length=32)] = None
    full_name: Annotated[str, Field(min_length=2, max_length=160)]
    default_project_id: UUID | None = None
    default_site_id: UUID | None = None

    @model_validator(mode="after")
    def _needs_an_identifier(self) -> UserInvite:
        if not self.email and not self.phone:
            raise ValueError("Provide an email, a phone number, or both")
        return self


class UserAdminUpdate(ApiModel):
    full_name: Annotated[str | None, Field(min_length=2, max_length=160)] = None
    email: Annotated[str | None, Field(max_length=160)] = None
    phone: Annotated[str | None, Field(max_length=32)] = None
    default_project_id: UUID | None = None
    default_site_id: UUID | None = None
    locale: Annotated[str | None, Field(max_length=16)] = None
    timezone: Annotated[str | None, Field(max_length=64)] = None


class UserDeactivate(ApiModel):
    reason: Annotated[str | None, Field(max_length=500)] = None


class UserAdminRead(ApiModel):
    id: UUID
    full_name: str
    display_name: str | None
    email: str | None
    phone: str | None
    status: str
    must_change_password: bool
    default_project_id: UUID | None
    default_site_id: UUID | None
    last_login_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class UserInviteResponse(ApiModel):
    user: UserAdminRead
    message: str = "User invited. They can set a password using the reset link."


class PasswordResetIssued(ApiModel):
    user: UserAdminRead
    message: str = "Password reset issued."


# -----------------------------------------------------------------------------
# Role grants
# -----------------------------------------------------------------------------


class RoleGrantCreate(ApiModel):
    role_id: UUID
    scope_type: ScopeType
    scope_id: UUID | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    reason: Annotated[str | None, Field(max_length=300)] = None

    @model_validator(mode="after")
    def _scope_id_matches_scope_type(self) -> RoleGrantCreate:
        needs_id = self.scope_type not in (ScopeType.GLOBAL, ScopeType.COMPANY)
        if needs_id and self.scope_id is None:
            raise ValueError(f"scope_type {self.scope_type.value} requires scope_id")
        if not needs_id and self.scope_id is not None:
            raise ValueError(f"scope_type {self.scope_type.value} must not carry scope_id")
        if self.valid_to and self.valid_from and self.valid_to < self.valid_from:
            raise ValueError("valid_to cannot be before valid_from")
        return self


class RoleGrantRevoke(ApiModel):
    reason: Annotated[str | None, Field(max_length=300)] = None


class RoleGrantRead(ApiModel):
    id: UUID
    user_id: UUID
    role_id: UUID
    role_code: str | None = None
    role_name: str | None = None
    scope_type: str
    scope_id: UUID | None
    valid_from: date | None
    valid_to: date | None
    revoked_at: date | None
    grant_reason: str | None
    created_at: datetime

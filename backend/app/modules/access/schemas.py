"""Access-control request and response schemas: roles and permissions."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PermissionRead(ApiModel):
    code: str
    module: str
    action: str
    description: str
    is_restricted: bool


class RoleCreate(ApiModel):
    code: Annotated[str, Field(min_length=2, max_length=50, pattern=r"^[A-Z0-9_]+$")]
    name: Annotated[str, Field(min_length=2, max_length=120)]
    description: Annotated[str | None, Field(max_length=400)] = None
    allowed_scope_types: list[str] = Field(default_factory=lambda: ["COMPANY"])


class RoleRead(ApiModel):
    id: UUID
    code: str
    name: str
    description: str | None
    is_system: bool
    is_locked: bool
    is_assignable: bool
    is_read_only: bool
    allowed_scope_types: list[str]
    version: int
    created_at: datetime


class RoleDetail(RoleRead):
    permission_codes: list[str] = Field(default_factory=list)


class RolePermissionsUpdate(ApiModel):
    """Replaces the role's permission set wholesale — the caller sends the
    complete list they want the role to hold, not a diff."""

    permission_codes: list[str] = Field(default_factory=list)


def _rebuild() -> None:
    RoleDetail.model_rebuild()


_rebuild()

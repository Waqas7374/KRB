"""Business-rule API contract."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.rules.domain.enums import RuleType


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class RuleCreate(ApiModel):
    rule_type: RuleType
    name: Annotated[str | None, Field(max_length=160)] = None
    description: Annotated[str | None, Field(max_length=500)] = None
    # Empty means the company-wide default for this rule type.
    scope: dict[str, str] = {}
    condition: Any = None
    value: dict[str, Any]
    priority: Annotated[int, Field(ge=-1000, le=1000)] = 0
    effective_from: date
    effective_to: date | None = None
    is_active: bool = True


class RuleUpdate(ApiModel):
    """Only what may change. Type, scope and start date are fixed: end this rule
    and create another to change what it applies to."""

    name: Annotated[str | None, Field(max_length=160)] = None
    description: Annotated[str | None, Field(max_length=500)] = None
    value: dict[str, Any] | None = None
    condition: Any = None
    priority: Annotated[int | None, Field(ge=-1000, le=1000)] = None
    effective_to: date | None = None
    is_active: bool | None = None


class RuleRead(ApiModel):
    id: UUID
    rule_type: str
    rule_type_label: str | None = None
    name: str | None
    description: str | None
    scope: dict[str, Any]
    condition: Any
    value: dict[str, Any]
    priority: int
    effective_from: date
    effective_to: date | None
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class RuleTypeRead(ApiModel):
    rule_type: str
    label: str
    description: str
    scope_keys: list[str]
    example: dict[str, Any]
    value_schema: dict[str, Any]


class ResolveRequest(ApiModel):
    rule_type: RuleType
    context: dict[str, Any] = {}
    at: date | None = None


class VerdictRead(ApiModel):
    rule_id: UUID
    name: str | None
    scope: dict[str, Any]
    priority: int
    specificity: int
    applies: bool
    reason: str
    winner: bool = False


class ResolveResponse(ApiModel):
    at: date
    winner: RuleRead | None
    considered: list[VerdictRead]

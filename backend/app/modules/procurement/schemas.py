"""Procurement API contract."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.procurement.domain.enums import PurchaseRequestPriority

Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PurchaseRequestItemIn(ApiModel):
    material_id: UUID
    quantity: Quantity
    unit_id: UUID
    estimated_rate: Money | None = None
    description: Annotated[str | None, Field(max_length=500)] = None
    required_date: date | None = None


class PurchaseRequestCreate(ApiModel):
    project_id: UUID
    site_id: UUID | None = None
    department_id: UUID | None = None
    cost_center_id: UUID | None = None
    phase_id: UUID | None = None
    required_date: date | None = None
    priority: PurchaseRequestPriority = PurchaseRequestPriority.NORMAL
    justification: Annotated[str, Field(min_length=5, max_length=2000)]
    items: Annotated[list[PurchaseRequestItemIn], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def _one_line_per_material_and_unit(self) -> PurchaseRequestCreate:
        seen: set[tuple[UUID, UUID]] = set()
        for item in self.items:
            key = (item.material_id, item.unit_id)
            if key in seen:
                raise ValueError("The same material and unit appear on two lines; combine them")
            seen.add(key)
        return self


class PurchaseRequestUpdate(PurchaseRequestCreate):
    """Replace the request's content. Lines are replaced as a set: a request is
    edited only while it is the author's (draft, rejected, changes requested),
    so there is no line history worth preserving row by row."""


class PurchaseRequestCancel(ApiModel):
    reason: Annotated[str, Field(min_length=5, max_length=500)]


class PurchaseRequestItemRead(ApiModel):
    id: UUID
    line_no: int
    material_id: UUID
    material_sku: str | None = None
    material_name: str | None = None
    description: str | None
    quantity: Decimal
    unit_id: UUID
    unit_code: str | None = None
    estimated_rate: Decimal | None
    estimated_amount: Decimal
    required_date: date | None
    sourced_quantity: Decimal


class PurchaseRequestListItem(ApiModel):
    id: UUID
    pr_number: str
    status: str
    priority: str
    project_id: UUID
    project_code: str | None = None
    site_id: UUID | None
    site_code: str | None = None
    required_date: date | None
    estimated_amount: Decimal
    currency_code: str
    item_count: int = 0
    requested_by_id: UUID | None
    requested_by_name: str | None = None
    submitted_at: datetime | None
    updated_at: datetime


class PurchaseRequestRead(ApiModel):
    id: UUID
    pr_number: str
    status: str
    priority: str
    project_id: UUID
    project_code: str | None = None
    project_name: str | None = None
    site_id: UUID | None
    site_code: str | None = None
    site_name: str | None = None
    department_id: UUID | None
    cost_center_id: UUID | None
    phase_id: UUID | None
    phase_code: str | None = None
    required_date: date | None
    justification: str
    currency_code: str
    estimated_amount: Decimal
    requested_by_id: UUID | None
    requested_by_name: str | None = None
    submitted_at: datetime | None
    approved_at: datetime | None
    decision_reason: str | None
    approval_request_id: UUID | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    items: list[PurchaseRequestItemRead]
    # What the caller may do now, so the UI does not re-derive the rules.
    can_edit: bool = False
    can_submit: bool = False
    can_cancel: bool = False

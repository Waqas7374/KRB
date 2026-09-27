"""Vendor-rate API contract.

There is deliberately no update schema for a rate's value: a rate is never
edited, a new period supersedes it (docs/05 §4).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.rates.domain.enums import RateSource

Rate = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=6)]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class RateCreate(ApiModel):
    vendor_id: UUID
    material_id: UUID
    unit_id: UUID
    rate: Rate
    currency_code: Annotated[str, Field(min_length=3, max_length=3)] = "PKR"
    # Neither set: company-wide. project_id: that project. site_id: that site
    # (its project is filled in).
    project_id: UUID | None = None
    site_id: UUID | None = None
    effective_from: date
    reason: Annotated[str | None, Field(max_length=500)] = None
    source: RateSource = RateSource.MANUAL


class RateNotes(ApiModel):
    notes: Annotated[str | None, Field(max_length=500)] = None


class RateRead(ApiModel):
    id: UUID
    vendor_id: UUID
    vendor_code: str | None = None
    vendor_name: str | None = None
    material_id: UUID
    material_sku: str | None = None
    material_name: str | None = None
    unit_id: UUID
    unit_code: str | None = None
    rate: Decimal
    currency_code: str
    project_id: UUID | None
    project_code: str | None = None
    site_id: UUID | None
    site_code: str | None = None
    scope: str = "Company-wide"
    effective_from: date
    effective_to: date | None
    is_current: bool = False
    status: str
    source: str
    reason: str | None
    notes: str | None
    previous_rate: Decimal | None
    change_pct: Decimal | None
    requested_by_id: UUID | None
    requested_by_name: str | None = None
    submitted_at: datetime | None
    approved_at: datetime | None
    decision_reason: str | None
    approval_request_id: UUID | None
    version: int
    created_at: datetime
    updated_at: datetime
    can_withdraw: bool = False


class RatePoint(ApiModel):
    effective_from: date
    rate: Decimal


class RateGridRow(ApiModel):
    """One vendor's current price for one material in one scope, with the road it took."""

    rate_id: UUID
    vendor_id: UUID
    vendor_code: str | None = None
    vendor_name: str | None = None
    material_id: UUID
    material_sku: str | None = None
    material_name: str | None = None
    unit_id: UUID
    unit_code: str | None = None
    scope: str
    currency_code: str
    rate: Decimal
    effective_from: date
    previous_rate: Decimal | None
    change_pct: Decimal | None
    # Oldest first: the periods that have stood, for a sparkline.
    points: list[RatePoint]
    has_pending: bool = False


class RateHistoryRead(ApiModel):
    id: UUID
    vendor_rate_id: UUID
    vendor_id: UUID
    vendor_name: str | None = None
    material_id: UUID
    material_name: str | None = None
    old_rate: Decimal | None
    new_rate: Decimal
    change_pct: Decimal | None
    effective_from: date
    reason: str | None
    changed_by_id: UUID | None
    changed_by_name: str | None = None
    changed_at: datetime


class ResolvedRateRead(ApiModel):
    rate_id: UUID
    rate: Decimal
    unit_id: UUID
    unit_code: str | None = None
    currency_code: str
    scope: str
    effective_from: date
    effective_to: date | None
    source: str

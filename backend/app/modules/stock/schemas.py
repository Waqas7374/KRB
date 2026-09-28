"""Stock issue, transfer and adjustment API contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.stock.domain.enums import AdjustmentReason, IssuedToType


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class WarehouseOptionRead(ApiModel):
    id: UUID
    code: str
    name: str
    site_id: UUID
    site_code: str | None = None
    is_default_receiving: bool


class CancelBody(ApiModel):
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class LineIn(ApiModel):
    material_id: UUID
    quantity: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
    unit_id: UUID
    remarks: Annotated[str | None, Field(max_length=300)] = None


class LineRead(ApiModel):
    id: UUID
    line_no: int
    material_id: UUID
    material_sku: str | None = None
    material_name: str | None = None
    unit_id: UUID
    unit_code: str | None = None
    quantity: Decimal
    base_quantity: Decimal | None
    base_unit_code: str | None = None
    # None for a reader without inventory.view_valuation.
    unit_cost: Decimal | None = None
    remarks: str | None


# -----------------------------------------------------------------------------
# Issues
# -----------------------------------------------------------------------------


class IssueCreate(ApiModel):
    warehouse_id: UUID
    issued_to_type: IssuedToType
    issued_to_name: Annotated[str, Field(min_length=2, max_length=160)]
    purpose: Annotated[str, Field(min_length=3, max_length=300)]
    issue_date: date | None = None
    remarks: Annotated[str | None, Field(max_length=1000)] = None
    lines: Annotated[list[LineIn], Field(min_length=1, max_length=100)]


class IssueLineRead(LineRead):
    value: Decimal | None = None


class IssueListItem(ApiModel):
    id: UUID
    issue_number: str
    status: str
    warehouse_id: UUID
    warehouse_code: str | None = None
    site_id: UUID
    site_code: str | None = None
    issued_to_type: str
    issued_to_name: str
    purpose: str
    issue_date: date
    issued_at: datetime | None
    updated_at: datetime


class IssueRead(IssueListItem):
    project_id: UUID | None
    warehouse_name: str | None = None
    issued_by_id: UUID | None
    journal_entry_id: UUID | None = None
    cancelled_at: datetime | None
    cancel_reason: str | None
    remarks: str | None
    version: int
    created_at: datetime
    prices_hidden: bool = False
    total_value: Decimal | None = None
    items: list[IssueLineRead]
    can_post: bool = False
    can_cancel: bool = False


# -----------------------------------------------------------------------------
# Transfers
# -----------------------------------------------------------------------------


class TransferCreate(ApiModel):
    from_warehouse_id: UUID
    to_warehouse_id: UUID
    transfer_date: date | None = None
    vehicle_number: Annotated[str | None, Field(max_length=30)] = None
    remarks: Annotated[str | None, Field(max_length=1000)] = None
    lines: Annotated[list[LineIn], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def _different_stores(self) -> TransferCreate:
        if self.from_warehouse_id == self.to_warehouse_id:
            raise ValueError("A transfer needs two different warehouses")
        return self


class TransferListItem(ApiModel):
    id: UUID
    transfer_number: str
    status: str
    from_warehouse_id: UUID
    from_warehouse_code: str | None = None
    to_warehouse_id: UUID
    to_warehouse_code: str | None = None
    site_id: UUID
    site_code: str | None = None
    to_site_id: UUID
    to_site_code: str | None = None
    transfer_date: date
    vehicle_number: str | None
    dispatched_at: datetime | None
    received_at: datetime | None
    updated_at: datetime


class TransferRead(TransferListItem):
    project_id: UUID | None
    remarks: str | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    version: int
    created_at: datetime
    prices_hidden: bool = False
    items: list[LineRead]
    can_dispatch: bool = False
    can_receive: bool = False
    can_cancel: bool = False


# -----------------------------------------------------------------------------
# Adjustments
# -----------------------------------------------------------------------------


class AdjustmentLineIn(ApiModel):
    material_id: UUID
    # Signed, in the material's base unit: + adds stock, - removes it.
    quantity_delta: Annotated[Decimal, Field(max_digits=18, decimal_places=4)]
    # What an increase is worth per base unit. Left out, the current average
    # cost is used; when there is no stock to take an average from, it is needed.
    unit_cost: Annotated[Decimal | None, Field(ge=0, max_digits=18, decimal_places=6)] = None
    remarks: Annotated[str | None, Field(max_length=300)] = None

    @model_validator(mode="after")
    def _not_zero(self) -> AdjustmentLineIn:
        if self.quantity_delta == 0:
            raise ValueError("An adjustment line must change the quantity")
        return self


class AdjustmentCreate(ApiModel):
    warehouse_id: UUID
    reason_code: AdjustmentReason
    reason_note: Annotated[str, Field(min_length=5, max_length=500)]
    adjustment_date: date | None = None
    lines: Annotated[list[AdjustmentLineIn], Field(min_length=1, max_length=100)]


class AdjustmentLineRead(ApiModel):
    id: UUID
    line_no: int
    material_id: UUID
    material_sku: str | None = None
    material_name: str | None = None
    unit_code: str | None = None
    quantity_delta: Decimal
    system_quantity: Decimal | None
    # None for a reader without inventory.view_valuation.
    unit_cost: Decimal | None = None
    value_delta: Decimal | None = None
    remarks: str | None


class AdjustmentListItem(ApiModel):
    id: UUID
    adjustment_number: str
    status: str
    warehouse_id: UUID
    warehouse_code: str | None = None
    site_id: UUID
    site_code: str | None = None
    reason_code: str
    adjustment_date: date
    posted_at: datetime | None
    updated_at: datetime


class AdjustmentRead(AdjustmentListItem):
    project_id: UUID | None
    warehouse_name: str | None = None
    reason_note: str
    approval_request_id: UUID | None
    submitted_at: datetime | None
    decision_reason: str | None
    journal_entry_id: UUID | None = None
    cancelled_at: datetime | None
    cancel_reason: str | None
    version: int
    created_at: datetime
    prices_hidden: bool = False
    net_value: Decimal | None = None
    items: list[AdjustmentLineRead]
    can_edit: bool = False
    can_submit: bool = False
    can_withdraw: bool = False
    can_cancel: bool = False

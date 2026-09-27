"""GRN API contract."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class CounterPurchaseLineIn(ApiModel):
    material_id: UUID
    quantity: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
    unit_id: UUID
    # What the bill says one unit cost: the person at the counter is the only one who knows.
    rate: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
    batch_no: Annotated[str | None, Field(max_length=60)] = None


class CounterPurchaseCreate(ApiModel):
    warehouse_id: UUID
    vendor_id: UUID
    # The bill or receipt number: what makes a purchase with no delivery checkable.
    reference: Annotated[str, Field(min_length=2, max_length=60)]
    received_date: date | None = None
    remarks: Annotated[str | None, Field(max_length=1000)] = None
    lines: Annotated[list[CounterPurchaseLineIn], Field(min_length=1, max_length=100)]


class GrnFromDelivery(ApiModel):
    # Left out, the site's default receiving warehouse is used.
    warehouse_id: UUID | None = None


class LineInspectionIn(ApiModel):
    grn_item_id: UUID
    accepted_quantity: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)]
    rejection_reason: Annotated[str | None, Field(max_length=300)] = None
    batch_no: Annotated[str | None, Field(max_length=60)] = None
    expiry_date: date | None = None


class InspectionIn(ApiModel):
    lines: Annotated[list[LineInspectionIn], Field(min_length=1, max_length=200)]
    warehouse_id: UUID | None = None
    remarks: Annotated[str | None, Field(max_length=1000)] = None


class CancelBody(ApiModel):
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class GrnItemRead(ApiModel):
    id: UUID
    line_no: int
    material_id: UUID
    material_sku: str | None = None
    material_name: str | None = None
    unit_id: UUID
    unit_code: str | None = None
    ordered_quantity: Decimal | None
    delivered_quantity: Decimal
    accepted_quantity: Decimal
    rejected_quantity: Decimal
    rejection_reason: str | None
    rate: Decimal | None
    amount: Decimal | None
    base_quantity: Decimal | None
    base_unit_code: str | None = None
    unit_cost: Decimal | None
    batch_no: str | None
    expiry_date: date | None
    inventory_txn_id: UUID | None


class GrnListItem(ApiModel):
    id: UUID
    grn_number: str
    status: str
    inspection_result: str
    site_id: UUID
    site_code: str | None = None
    warehouse_id: UUID
    warehouse_code: str | None = None
    vendor_id: UUID
    vendor_name: str | None = None
    delivery_id: UUID | None
    delivery_number: str | None = None
    purchase_order_id: UUID | None
    counter_reference: str | None = None
    is_counter_purchase: bool = False
    received_date: date
    net_amount: Decimal | None = None
    posted_at: datetime | None
    updated_at: datetime


class GrnRead(ApiModel):
    id: UUID
    grn_number: str
    status: str
    inspection_result: str
    delivery_id: UUID | None
    delivery_number: str | None = None
    purchase_order_id: UUID | None
    purchase_order_number: str | None = None
    counter_reference: str | None = None
    is_counter_purchase: bool = False
    vendor_id: UUID
    vendor_name: str | None = None
    project_id: UUID | None
    site_id: UUID
    site_code: str | None = None
    warehouse_id: UUID
    warehouse_code: str | None = None
    warehouse_name: str | None = None
    received_date: date
    gross_amount: Decimal | None = None
    net_amount: Decimal | None = None
    prices_hidden: bool = False
    posted_at: datetime | None
    posted_by_id: UUID | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    remarks: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    items: list[GrnItemRead]
    can_inspect: bool = False
    can_reprice: bool = False
    can_post: bool = False
    can_cancel: bool = False
    has_unpriced_lines: bool = False

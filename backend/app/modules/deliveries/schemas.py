"""Delivery API contract."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.deliveries.domain.enums import LocationSource

Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class DeliveryLineIn(ApiModel):
    material_id: UUID
    quantity: Quantity
    unit_id: UUID
    remarks: Annotated[str | None, Field(max_length=500)] = None


class DeliveryCreate(ApiModel):
    """A delivery as captured. There is deliberately no rate, amount, factor or
    flag here: the server resolves and computes all of them."""

    # Client-generated (UUIDv7 on the phone): the idempotency key. Pushing the
    # same id twice returns the stored delivery and changes nothing.
    id: UUID | None = None
    site_id: UUID
    vendor_id: UUID
    purchase_order_id: UUID | None = None
    po_item_id: UUID | None = None
    truck_number: Annotated[str | None, Field(max_length=30)] = None
    truck_type_id: UUID | None = None
    driver_name: Annotated[str | None, Field(max_length=120)] = None
    driver_phone: Annotated[str | None, Field(max_length=32)] = None
    challan_number: Annotated[str | None, Field(max_length=60)] = None
    challan_date: date | None = None
    # Left out, the server's clock at receipt is used (a web entry).
    captured_at: datetime | None = None
    latitude: Annotated[Decimal | None, Field(ge=-90, le=90)] = None
    longitude: Annotated[Decimal | None, Field(ge=-180, le=180)] = None
    gps_accuracy_m: Annotated[Decimal | None, Field(ge=0, le=100000)] = None
    location_source: LocationSource = LocationSource.GPS
    remarks: Annotated[str | None, Field(max_length=1000)] = None
    items: Annotated[list[DeliveryLineIn], Field(min_length=1, max_length=50)]
    device_id: Annotated[str | None, Field(max_length=80)] = None
    app_version: Annotated[str | None, Field(max_length=30)] = None
    was_offline: bool = False
    device_time: datetime | None = None


class QuantityRead(ApiModel):
    unit_code: str
    quantity: Decimal


class DayPointRead(ApiModel):
    day: date
    deliveries: int
    # In tonnes. Loads counted in other units add to `quantities`, never to this.
    tonnage: Decimal
    value: Decimal | None = None


class RankRead(ApiModel):
    id: UUID
    label: str
    sublabel: str | None = None
    deliveries: int
    quantities: list[QuantityRead] = []
    value: Decimal | None = None


class WaitingRead(ApiModel):
    id: UUID
    delivery_number: str
    captured_at: datetime
    site_code: str | None = None
    vendor_name: str | None = None
    flag_count: int
    status: str


class DeliverySummaryRead(ApiModel):
    """The material-delivery dashboard: one screen's worth of figures."""

    from_date: date
    to_date: date
    deliveries: int
    by_status: dict[str, int]
    open_flags: int
    tonnage: Decimal
    quantities: list[QuantityRead]
    # None for a reader without rates.view.
    value: Decimal | None = None
    values_hidden: bool = False
    by_day: list[DayPointRead]
    top_materials: list[RankRead]
    top_vendors: list[RankRead]
    by_site: list[RankRead]
    waiting: list[WaitingRead]
    waiting_total: int


class DeliveryItemRead(ApiModel):
    id: UUID
    line_no: int
    material_id: UUID
    material_sku: str | None = None
    material_name: str | None = None
    quantity: Decimal
    unit_id: UUID
    unit_code: str | None = None
    converted_quantity: Decimal | None
    converted_unit_code: str | None = None
    conversion_factor: Decimal | None
    # None for a reader without `rates.view`.
    rate: Decimal | None = None
    rate_source: str | None = None
    amount: Decimal | None = None
    remarks: str | None


class FlagRead(ApiModel):
    id: UUID
    flag_type: str
    severity: str
    message: str
    expected_value: Decimal | None
    actual_value: Decimal | None
    deviation_pct: Decimal | None
    rule_id: UUID | None
    rule_snapshot: dict[str, object]
    status: str
    resolved_by_id: UUID | None
    resolved_at: datetime | None
    resolution_note: str | None
    created_at: datetime


class DeliveryListItem(ApiModel):
    id: UUID
    delivery_number: str
    status: str
    site_id: UUID
    site_code: str | None = None
    project_code: str | None = None
    vendor_id: UUID
    vendor_name: str | None = None
    truck_number: str | None
    challan_number: str | None
    captured_at: datetime
    flag_count: int
    has_open_flags: bool
    worst_severity: str | None = None
    material_summary: str | None = None
    amount: Decimal | None = None
    purchase_order_id: UUID | None
    updated_at: datetime


class DeliveryRead(ApiModel):
    id: UUID
    delivery_number: str
    status: str
    project_id: UUID | None
    project_code: str | None = None
    site_id: UUID
    site_code: str | None = None
    site_name: str | None = None
    vendor_id: UUID
    vendor_code: str | None = None
    vendor_name: str | None = None
    purchase_order_id: UUID | None
    purchase_order_number: str | None = None
    truck_number: str | None
    truck_type_id: UUID | None
    truck_type_name: str | None = None
    driver_name: str | None
    driver_phone: str | None
    challan_number: str | None
    challan_date: date | None
    captured_lat: Decimal | None
    captured_lng: Decimal | None
    gps_accuracy_m: Decimal | None
    location_source: str
    distance_from_site_m: Decimal | None
    is_inside_geofence: bool | None
    captured_at: datetime
    received_at: datetime
    clock_skew_seconds: int | None
    device_id: str | None
    app_version: str | None
    was_offline: bool
    submitted_by_id: UUID | None
    submitted_by_name: str | None = None
    submitted_at: datetime | None
    reviewed_by_id: UUID | None
    reviewed_at: datetime | None
    approved_by_id: UUID | None
    approved_at: datetime | None
    rejected_by_id: UUID | None
    rejected_at: datetime | None
    rejection_reason: str | None
    remarks: str | None
    flag_count: int
    has_open_flags: bool
    grn_id: UUID | None
    prices_hidden: bool = False
    total_amount: Decimal | None = None
    version: int
    created_at: datetime
    updated_at: datetime
    items: list[DeliveryItemRead]
    flags: list[FlagRead]
    reviews: list[ReviewRead] = []
    # What the caller may do now, so the screen never re-derives the rules.
    can_approve: bool = False
    can_reject: bool = False
    can_request_correction: bool = False
    can_reopen: bool = False
    can_attach_order: bool = False
    can_correct: bool = False
    can_waive: bool = False


class ReasonBody(ApiModel):
    comments: Annotated[str | None, Field(max_length=1000)] = None


class WaiveFlag(ApiModel):
    note: Annotated[str, Field(min_length=1, max_length=500)]


class AttachOrder(ApiModel):
    purchase_order_id: UUID
    comments: Annotated[str | None, Field(max_length=500)] = None


class ReviewRead(ApiModel):
    id: UUID
    action: str
    reviewer_id: UUID | None
    reviewer_name: str | None
    reviewed_at: datetime
    comments: str | None
    previous_status: str
    new_status: str


class IngestResponse(ApiModel):
    """201 when recorded, 200 when the same id was already recorded."""

    outcome: str
    delivery: DeliveryRead

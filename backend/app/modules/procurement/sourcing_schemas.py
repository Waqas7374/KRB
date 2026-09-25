"""RFQ, quotation and purchase-order API contract."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)]
Percent = Annotated[Decimal, Field(ge=0, le=100, max_digits=7, decimal_places=4)]
Reason = Annotated[str, Field(min_length=5, max_length=500)]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ReasonBody(ApiModel):
    reason: Reason


# -----------------------------------------------------------------------------
# RFQ
# -----------------------------------------------------------------------------


class RfqItemIn(ApiModel):
    material_id: UUID
    quantity: Quantity
    unit_id: UUID
    description: Annotated[str | None, Field(max_length=500)] = None
    pr_item_id: UUID | None = None


class RfqCreate(ApiModel):
    """Either raise an RFQ from an approved purchase request (its unsourced
    lines are used when `items` is omitted), or state project, site and lines."""

    purchase_request_id: UUID | None = None
    project_id: UUID | None = None
    site_id: UUID | None = None
    title: Annotated[str, Field(min_length=3, max_length=200)]
    due_date: date | None = None
    terms: Annotated[str | None, Field(max_length=4000)] = None
    items: Annotated[list[RfqItemIn] | None, Field(max_length=200)] = None
    vendor_ids: Annotated[list[UUID], Field(max_length=50)] = []

    @model_validator(mode="after")
    def _has_a_source(self) -> RfqCreate:
        if self.purchase_request_id is None and (self.project_id is None or not self.items):
            raise ValueError(
                "Give a purchase_request_id, or a project_id together with the lines to quote"
            )
        return self


class RfqUpdate(ApiModel):
    title: Annotated[str, Field(min_length=3, max_length=200)]
    due_date: date | None = None
    terms: Annotated[str | None, Field(max_length=4000)] = None
    items: Annotated[list[RfqItemIn], Field(min_length=1, max_length=200)]


class RfqInviteVendors(ApiModel):
    vendor_ids: Annotated[list[UUID], Field(min_length=1, max_length=50)]


class RfqItemRead(ApiModel):
    id: UUID
    line_no: int
    material_id: UUID
    material_sku: str | None = None
    material_name: str | None = None
    description: str | None
    quantity: Decimal
    unit_id: UUID
    unit_code: str | None = None
    pr_item_id: UUID | None
    estimated_rate: Decimal | None = None


class RfqVendorRead(ApiModel):
    id: UUID
    vendor_id: UUID
    vendor_code: str | None = None
    vendor_name: str | None = None
    status: str
    invited_at: datetime
    responded_at: datetime | None
    quotation_id: UUID | None = None
    quotation_number: str | None = None
    quotation_status: str | None = None
    quotation_total: Decimal | None = None


class RfqListItem(ApiModel):
    id: UUID
    rfq_number: str
    title: str
    status: str
    project_id: UUID
    project_code: str | None = None
    site_id: UUID | None
    site_code: str | None = None
    purchase_request_id: UUID | None
    pr_number: str | None = None
    issue_date: date | None
    due_date: date | None
    item_count: int = 0
    vendor_count: int = 0
    quotation_count: int = 0
    updated_at: datetime


class RfqRead(ApiModel):
    id: UUID
    rfq_number: str
    title: str
    status: str
    project_id: UUID
    project_code: str | None = None
    project_name: str | None = None
    site_id: UUID | None
    site_code: str | None = None
    purchase_request_id: UUID | None
    pr_number: str | None = None
    issue_date: date | None
    due_date: date | None
    terms: str | None
    currency_code: str
    closed_at: datetime | None
    close_reason: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    items: list[RfqItemRead]
    vendors: list[RfqVendorRead]
    can_edit: bool = False
    can_issue: bool = False
    can_close: bool = False
    can_cancel: bool = False
    can_record_quotation: bool = False


# -----------------------------------------------------------------------------
# Quotations
# -----------------------------------------------------------------------------


class QuotationItemIn(ApiModel):
    rfq_item_id: UUID
    rate: Money
    discount_pct: Percent = Decimal(0)
    tax_pct: Percent = Decimal(0)
    # Omit to quote the full requested quantity.
    quantity: Quantity | None = None
    delivery_days: Annotated[int | None, Field(ge=0, le=3650)] = None
    remarks: Annotated[str | None, Field(max_length=500)] = None


class QuotationWrite(ApiModel):
    vendor_id: UUID
    vendor_reference: Annotated[str | None, Field(max_length=80)] = None
    quote_date: date
    valid_until: date | None = None
    delivery_days: Annotated[int | None, Field(ge=0, le=3650)] = None
    payment_terms: Annotated[str | None, Field(max_length=200)] = None
    notes: Annotated[str | None, Field(max_length=2000)] = None
    items: Annotated[list[QuotationItemIn], Field(min_length=1, max_length=200)]


class SelectQuotation(ApiModel):
    # Mandatory (§10), whatever the price. The service enforces a real sentence.
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class QuotationItemRead(ApiModel):
    id: UUID
    line_no: int
    rfq_item_id: UUID
    material_id: UUID
    material_sku: str | None = None
    material_name: str | None = None
    quantity: Decimal
    unit_id: UUID
    unit_code: str | None = None
    rate: Decimal
    discount_pct: Decimal
    tax_pct: Decimal
    net_rate: Decimal | None = None
    line_total: Decimal
    delivery_days: int | None
    remarks: str | None


class QuotationListItem(ApiModel):
    id: UUID
    quotation_number: str
    rfq_id: UUID
    rfq_number: str | None = None
    vendor_id: UUID
    vendor_name: str | None = None
    status: str
    quote_date: date
    valid_until: date | None
    total_amount: Decimal
    currency_code: str
    updated_at: datetime


class QuotationRead(ApiModel):
    id: UUID
    quotation_number: str
    rfq_id: UUID
    rfq_number: str | None = None
    rfq_status: str | None = None
    vendor_id: UUID
    vendor_code: str | None = None
    vendor_name: str | None = None
    project_id: UUID
    site_id: UUID | None
    vendor_reference: str | None
    quote_date: date
    valid_until: date | None
    delivery_days: int | None
    payment_terms: str | None
    notes: str | None
    currency_code: str
    subtotal: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    status: str
    selection_reason: str | None
    selected_by_id: UUID | None
    selected_by_name: str | None = None
    selected_at: datetime | None
    reject_reason: str | None
    purchase_order_id: UUID | None = None
    purchase_order_number: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime
    items: list[QuotationItemRead]
    can_edit: bool = False
    can_select: bool = False
    can_withdraw: bool = False
    can_create_order: bool = False


class ComparisonCellRead(ApiModel):
    quotation_id: UUID
    vendor_id: UUID
    quantity: Decimal
    rate: Decimal
    discount_pct: Decimal
    tax_pct: Decimal
    net_rate: Decimal
    line_total: Decimal
    delivery_days: int | None
    remarks: str | None
    is_lowest: bool
    is_partial: bool


class ComparisonRowRead(ApiModel):
    rfq_item_id: UUID
    line_no: int
    material_id: UUID
    material_sku: str | None = None
    material_name: str | None = None
    quantity: Decimal
    unit_code: str | None = None
    estimated_rate: Decimal | None
    cells: dict[UUID, ComparisonCellRead]


class ComparisonColumnRead(ApiModel):
    vendor_id: UUID
    vendor_name: str | None = None
    rfq_vendor_id: UUID
    invitation_status: str
    quotation_id: UUID | None
    quotation_number: str | None
    quotation_status: str | None
    delivery_days: int | None
    payment_terms: str | None
    valid_until: date | None
    total_amount: Decimal | None
    lines_quoted: int
    covers_all: bool
    is_lowest_total: bool
    selection_reason: str | None = None


class ComparisonRead(ApiModel):
    rfq_id: UUID
    rfq_number: str
    rfq_status: str
    currency_code: str
    columns: list[ComparisonColumnRead]
    rows: list[ComparisonRowRead]
    can_select: bool = False


# -----------------------------------------------------------------------------
# Purchase orders
# -----------------------------------------------------------------------------


class PurchaseOrderItemIn(ApiModel):
    material_id: UUID
    quantity: Quantity
    unit_id: UUID
    rate: Money
    discount_pct: Percent = Decimal(0)
    tax_pct: Percent = Decimal(0)
    description: Annotated[str | None, Field(max_length=500)] = None
    pr_item_id: UUID | None = None


class PurchaseOrderWrite(ApiModel):
    vendor_id: UUID
    project_id: UUID
    site_id: UUID | None = None
    phase_id: UUID | None = None
    cost_center_id: UUID | None = None
    expected_delivery_date: date | None = None
    delivery_address: Annotated[str | None, Field(max_length=500)] = None
    payment_terms: Annotated[str | None, Field(max_length=200)] = None
    terms_and_conditions: Annotated[str | None, Field(max_length=4000)] = None
    items: Annotated[list[PurchaseOrderItemIn], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def _one_line_per_material_and_source(self) -> PurchaseOrderWrite:
        seen: set[tuple[UUID, UUID, UUID | None]] = set()
        for item in self.items:
            key = (item.material_id, item.unit_id, item.pr_item_id)
            if key in seen:
                raise ValueError("The same material and unit appear on two lines; combine them")
            seen.add(key)
        return self


class PurchaseOrderItemRead(ApiModel):
    id: UUID
    line_no: int
    material_id: UUID
    material_sku: str | None = None
    material_name: str | None = None
    description: str | None
    quantity: Decimal
    unit_id: UUID
    unit_code: str | None = None
    # None for a reader without procurement.po.view_pricing.
    rate: Decimal | None = None
    discount_pct: Decimal | None = None
    tax_pct: Decimal | None = None
    tax_amount: Decimal | None = None
    line_total: Decimal | None = None
    received_quantity: Decimal
    accepted_quantity: Decimal
    invoiced_quantity: Decimal
    pr_item_id: UUID | None
    pr_number: str | None = None


class PurchaseOrderListItem(ApiModel):
    id: UUID
    po_number: str
    revision: int
    status: str
    vendor_id: UUID
    vendor_name: str | None = None
    project_id: UUID
    project_code: str | None = None
    site_id: UUID | None
    site_code: str | None = None
    po_date: date
    expected_delivery_date: date | None
    total_amount: Decimal | None = None
    currency_code: str
    updated_at: datetime


class PurchaseOrderRead(ApiModel):
    id: UUID
    po_number: str
    revision: int
    amendment_reason: str | None
    status: str
    vendor_id: UUID
    vendor_code: str | None = None
    vendor_name: str | None = None
    project_id: UUID
    project_code: str | None = None
    project_name: str | None = None
    site_id: UUID | None
    site_code: str | None = None
    site_name: str | None = None
    phase_id: UUID | None
    cost_center_id: UUID | None
    quotation_id: UUID | None
    quotation_number: str | None = None
    rfq_id: UUID | None = None
    rfq_number: str | None = None
    po_date: date
    expected_delivery_date: date | None
    delivery_address: str | None
    payment_terms: str | None
    terms_and_conditions: str | None
    currency_code: str
    subtotal: Decimal | None = None
    discount_amount: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    prices_hidden: bool = False
    submitted_at: datetime | None
    approved_at: datetime | None
    decision_reason: str | None
    approval_request_id: UUID | None
    sent_at: datetime | None
    acknowledged_at: datetime | None
    closed_at: datetime | None
    close_reason: str | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    items: list[PurchaseOrderItemRead]
    can_edit: bool = False
    can_submit: bool = False
    can_amend: bool = False
    can_send: bool = False
    can_acknowledge: bool = False
    can_cancel: bool = False
    can_close: bool = False

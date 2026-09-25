"""Purchase requests: a site or project asks for materials; approved requests
are sourced through RFQs and purchase orders."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constraints import enum_check, non_negative, percentage, positive
from app.core.db import BaseModel, CompanyModel, VersionMixin
from app.modules.procurement.domain.enums import (
    PurchaseOrderStatus,
    PurchaseRequestPriority,
    PurchaseRequestStatus,
    QuotationStatus,
    RfqStatus,
    RfqVendorStatus,
)


class PurchaseRequest(CompanyModel, VersionMixin):
    __tablename__ = "purchase_requests"
    __audited__ = True

    pr_number: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=PurchaseRequestStatus.DRAFT.value
    )
    priority: Mapped[str] = mapped_column(
        String(10), nullable=False, default=PurchaseRequestPriority.NORMAL.value
    )

    # Where the materials are needed. project_id/site_id/department_id are
    # also the scope dimensions scope_filter narrows by.
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    site_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sites.id", ondelete="RESTRICT"), index=True
    )
    department_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT")
    )
    cost_center_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cost_centers.id", ondelete="RESTRICT")
    )
    phase_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project_phases.id", ondelete="RESTRICT")
    )

    required_date: Mapped[date | None] = mapped_column(Date)
    justification: Mapped[str] = mapped_column(String(2000), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="PKR")
    # Sum of line estimates. Drives approval routing, so it is recomputed by
    # the service on every edit rather than accepted from the client.
    estimated_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)

    requested_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(String(2000))
    approval_request_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))

    items: Mapped[list[PurchaseRequestItem]] = relationship(
        back_populates="request",
        order_by="PurchaseRequestItem.line_no",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "pr_number", name="uq_purchase_requests_number"),
        Index("ix_purchase_requests_status", "company_id", "status"),
        enum_check("status", PurchaseRequestStatus),
        enum_check("priority", PurchaseRequestPriority),
        non_negative("estimated_amount"),
    )


class PurchaseRequestItem(BaseModel):
    __tablename__ = "purchase_request_items"
    __audited__ = True

    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("purchase_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    material_id: Mapped[UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_id: Mapped[UUID] = mapped_column(
        ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )
    estimated_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    estimated_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    required_date: Mapped[date | None] = mapped_column(Date)
    # Closes the loop from purchase orders back to the request (docs/02).
    sourced_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)

    request: Mapped[PurchaseRequest] = relationship(back_populates="items", lazy="noload")

    __table_args__ = (
        UniqueConstraint("request_id", "line_no", name="uq_purchase_request_items_line"),
        positive("quantity"),
        non_negative("estimated_amount"),
        non_negative("sourced_quantity"),
        CheckConstraint(
            "estimated_rate IS NULL OR estimated_rate >= 0", name="estimated_rate_non_negative"
        ),
    )


# -----------------------------------------------------------------------------
# RFQs and quotations
# -----------------------------------------------------------------------------


class Rfq(CompanyModel, VersionMixin):
    """A request for quotation sent to invited vendors."""

    __tablename__ = "rfqs"
    __audited__ = True

    rfq_number: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RfqStatus.DRAFT.value)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    site_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sites.id", ondelete="RESTRICT"), index=True
    )
    purchase_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchase_requests.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    issue_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    terms: Mapped[str | None] = mapped_column(String(4000))
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="PKR")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(500))

    items: Mapped[list[RfqItem]] = relationship(
        back_populates="rfq",
        order_by="RfqItem.line_no",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    vendors: Mapped[list[RfqVendor]] = relationship(
        back_populates="rfq",
        order_by="RfqVendor.invited_at",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "rfq_number", name="uq_rfqs_number"),
        Index("ix_rfqs_status", "company_id", "status"),
        enum_check("status", RfqStatus),
    )


class RfqItem(BaseModel):
    __tablename__ = "rfq_items"
    __audited__ = True

    rfq_id: Mapped[UUID] = mapped_column(
        ForeignKey("rfqs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    material_id: Mapped[UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_id: Mapped[UUID] = mapped_column(
        ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )
    pr_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchase_request_items.id", ondelete="RESTRICT")
    )

    rfq: Mapped[Rfq] = relationship(back_populates="items", lazy="noload")

    __table_args__ = (
        UniqueConstraint("rfq_id", "line_no", name="uq_rfq_items_line"),
        positive("quantity"),
    )


class RfqVendor(BaseModel):
    """An invitation. `access_token_hash` is reserved for the vendor portal
    (docs/02): the column exists so the portal needs no schema change."""

    __tablename__ = "rfq_vendors"
    __audited__ = True

    rfq_id: Mapped[UUID] = mapped_column(
        ForeignKey("rfqs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RfqVendorStatus.INVITED.value
    )
    access_token_hash: Mapped[str | None] = mapped_column(String(128))

    rfq: Mapped[Rfq] = relationship(back_populates="vendors", lazy="noload")

    __table_args__ = (
        UniqueConstraint("rfq_id", "vendor_id", name="uq_rfq_vendors_vendor"),
        enum_check("status", RfqVendorStatus),
    )


class VendorQuotation(CompanyModel, VersionMixin):
    __tablename__ = "vendor_quotations"
    __audited__ = True

    quotation_number: Mapped[str] = mapped_column(String(40), nullable=False)
    rfq_id: Mapped[UUID] = mapped_column(
        ForeignKey("rfqs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    vendor_id: Mapped[UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Copied from the RFQ so scope_filter narrows quotations exactly as it
    # narrows the RFQ they answer.
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    site_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sites.id", ondelete="RESTRICT"), index=True
    )
    vendor_reference: Mapped[str | None] = mapped_column(String(80))
    quote_date: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)
    delivery_days: Mapped[int | None] = mapped_column(Integer)
    payment_terms: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(String(2000))
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="PKR")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=QuotationStatus.RECEIVED.value
    )
    # No automatic selection (§10): a person chooses, and must say why.
    selection_reason: Mapped[str | None] = mapped_column(String(2000))
    selected_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(String(500))

    items: Mapped[list[VendorQuotationItem]] = relationship(
        back_populates="quotation",
        order_by="VendorQuotationItem.line_no",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "quotation_number", name="uq_vendor_quotations_number"),
        UniqueConstraint("rfq_id", "vendor_id", name="uq_vendor_quotations_rfq_vendor"),
        # At most one winner per RFQ, enforced by the database rather than by
        # every code path that touches a quotation.
        Index(
            "uq_vendor_quotations_one_selected",
            "rfq_id",
            unique=True,
            postgresql_where=text("status = 'SELECTED'"),
        ),
        enum_check("status", QuotationStatus),
        CheckConstraint(
            "status <> 'SELECTED' OR (selection_reason IS NOT NULL AND selected_by_id IS NOT NULL)",
            name="selected_needs_reason",
        ),
        non_negative("total_amount"),
    )


class VendorQuotationItem(BaseModel):
    __tablename__ = "vendor_quotation_items"
    __audited__ = True

    quotation_id: Mapped[UUID] = mapped_column(
        ForeignKey("vendor_quotations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    rfq_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("rfq_items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    material_id: Mapped[UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_id: Mapped[UUID] = mapped_column(
        ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False, default=0)
    tax_pct: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False, default=0)
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    delivery_days: Mapped[int | None] = mapped_column(Integer)
    remarks: Mapped[str | None] = mapped_column(String(500))

    quotation: Mapped[VendorQuotation] = relationship(back_populates="items", lazy="noload")

    __table_args__ = (
        UniqueConstraint("quotation_id", "line_no", name="uq_vendor_quotation_items_line"),
        UniqueConstraint("quotation_id", "rfq_item_id", name="uq_vendor_quotation_items_rfq_item"),
        positive("quantity"),
        non_negative("rate"),
        percentage("discount_pct"),
        percentage("tax_pct"),
        non_negative("line_total"),
    )


# -----------------------------------------------------------------------------
# Purchase orders
# -----------------------------------------------------------------------------


class PurchaseOrder(CompanyModel, VersionMixin):
    __tablename__ = "purchase_orders"
    __audited__ = True

    po_number: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=PurchaseOrderStatus.DRAFT.value
    )
    # Bumped each time an approved order is reopened for amendment.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amendment_reason: Mapped[str | None] = mapped_column(String(1000))

    vendor_id: Mapped[UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    site_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sites.id", ondelete="RESTRICT"), index=True
    )
    phase_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project_phases.id", ondelete="RESTRICT")
    )
    cost_center_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cost_centers.id", ondelete="RESTRICT")
    )
    quotation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("vendor_quotations.id", ondelete="RESTRICT"), index=True
    )

    po_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_delivery_date: Mapped[date | None] = mapped_column(Date)
    delivery_address: Mapped[str | None] = mapped_column(String(500))
    payment_terms: Mapped[str | None] = mapped_column(String(200))
    terms_and_conditions: Mapped[str | None] = mapped_column(String(4000))
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="PKR")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(String(2000))
    approval_request_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(500))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))

    items: Mapped[list[PurchaseOrderItem]] = relationship(
        back_populates="order",
        order_by="PurchaseOrderItem.line_no",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "po_number", name="uq_purchase_orders_number"),
        Index("ix_purchase_orders_status", "company_id", "status"),
        enum_check("status", PurchaseOrderStatus),
        non_negative("total_amount"),
    )


class PurchaseOrderItem(BaseModel):
    __tablename__ = "purchase_order_items"
    __audited__ = True

    po_id: Mapped[UUID] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    material_id: Mapped[UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_id: Mapped[UUID] = mapped_column(
        ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False, default=0)
    tax_pct: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False, default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    # The three running totals that make 3-way matching possible without
    # recomputing history (docs/02). Written by goods receipt and invoicing.
    received_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    accepted_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    invoiced_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    pr_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchase_request_items.id", ondelete="RESTRICT"), index=True
    )

    order: Mapped[PurchaseOrder] = relationship(back_populates="items", lazy="noload")

    __table_args__ = (
        UniqueConstraint("po_id", "line_no", name="uq_purchase_order_items_line"),
        positive("quantity"),
        non_negative("rate"),
        percentage("discount_pct"),
        percentage("tax_pct"),
        non_negative("line_total"),
        non_negative("received_quantity"),
        non_negative("accepted_quantity"),
        non_negative("invoiced_quantity"),
    )

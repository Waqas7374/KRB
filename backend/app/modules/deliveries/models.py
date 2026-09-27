"""Deliveries, their lines, flags and reviews (docs/02 §6).

One truck movement is one delivery. The record is created once from the device
and never overwritten by it: rates and conversion factors are resolved on the
server and snapshotted onto the line, so a later change to either cannot
restate a delivery that was already counted and paid for.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constraints import enum_check, non_negative, positive
from app.core.db import BaseModel, CompanyModel, VersionMixin
from app.core.sync import SyncSeqMixin
from app.modules.deliveries.domain.enums import (
    DeliveryStatus,
    FlagSeverity,
    FlagStatus,
    FlagType,
    LocationSource,
    ReviewAction,
)


class Delivery(CompanyModel, VersionMixin, SyncSeqMixin):
    __tablename__ = "deliveries"
    __audited__ = True

    # Assigned on first successful ingest; the client UUID (`id`) is the
    # idempotency key until then.
    delivery_number: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=DeliveryStatus.SUBMITTED.value
    )

    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    vendor_id: Mapped[UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Nullable by decision Q3 (docs/12): a delivery may arrive with no order.
    purchase_order_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="RESTRICT"), index=True
    )
    po_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchase_order_items.id", ondelete="RESTRICT")
    )

    truck_number: Mapped[str | None] = mapped_column(String(30))
    truck_type_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("truck_types.id", ondelete="RESTRICT")
    )
    driver_name: Mapped[str | None] = mapped_column(String(120))
    driver_phone: Mapped[str | None] = mapped_column(String(32))
    challan_number: Mapped[str | None] = mapped_column(String(60))
    challan_date: Mapped[date | None] = mapped_column(Date)

    # Where the truck was when the entry was made.
    captured_lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    captured_lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    captured_point: Mapped[Any | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    gps_accuracy_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    location_source: Mapped[str] = mapped_column(
        String(10), nullable=False, default=LocationSource.GPS.value
    )
    distance_from_site_m: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    is_inside_geofence: Mapped[bool | None] = mapped_column(Boolean)

    # Device clock at capture, server clock at ingest: both kept, never "fixed".
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    clock_skew_seconds: Mapped[int | None] = mapped_column(Integer)

    device_id: Mapped[str | None] = mapped_column(String(80))
    app_version: Mapped[str | None] = mapped_column(String(30))
    was_offline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    submitted_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    reviewed_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(String(1000))
    remarks: Mapped[str | None] = mapped_column(String(1000))

    # Denormalised for the review queue's index.
    flag_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    has_open_flags: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    grn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))

    items: Mapped[list[DeliveryItem]] = relationship(
        back_populates="delivery",
        order_by="DeliveryItem.line_no",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    flags: Mapped[list[DeliveryFlag]] = relationship(
        back_populates="delivery",
        order_by="DeliveryFlag.created_at",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "delivery_number", name="uq_deliveries_number"),
        Index("ix_deliveries_site_captured", "company_id", "site_id", "captured_at"),
        Index("ix_deliveries_vendor_captured", "vendor_id", "captured_at"),
        Index(
            "ix_deliveries_review_queue",
            "company_id",
            "status",
            postgresql_where=text("has_open_flags"),
        ),
        enum_check("status", DeliveryStatus),
        enum_check("location_source", LocationSource),
        non_negative("flag_count"),
        CheckConstraint(
            "status <> 'REJECTED' OR rejection_reason IS NOT NULL",
            name="rejection_needs_reason",
        ),
    )


class DeliveryItem(BaseModel):
    __tablename__ = "delivery_items"
    __audited__ = True

    delivery_id: Mapped[UUID] = mapped_column(
        ForeignKey("deliveries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    material_id: Mapped[UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_id: Mapped[UUID] = mapped_column(
        ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )

    # Snapshotted, server-resolved values: the device never supplies them.
    converted_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    converted_unit_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("units.id", ondelete="RESTRICT")
    )
    conversion_factor: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    conversion_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    vendor_rate_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    rate_source: Mapped[str | None] = mapped_column(String(12))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    # The order line this load counts against, when the delivery has an order.
    po_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchase_order_items.id", ondelete="RESTRICT"), index=True
    )
    remarks: Mapped[str | None] = mapped_column(String(500))

    delivery: Mapped[Delivery] = relationship(back_populates="items", lazy="noload")

    __table_args__ = (
        UniqueConstraint("delivery_id", "line_no", name="uq_delivery_items_line"),
        positive("quantity"),
        CheckConstraint("amount IS NULL OR amount >= 0", name="amount_non_negative"),
    )


class DeliveryFlag(BaseModel):
    __tablename__ = "delivery_flags"
    __audited__ = True

    delivery_id: Mapped[UUID] = mapped_column(
        ForeignKey("deliveries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    flag_type: Mapped[str] = mapped_column(String(30), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    # Which configured rule fired, and what it said at the time.
    rule_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    rule_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    expected_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    actual_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    deviation_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 2))
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=FlagStatus.OPEN.value)
    resolved_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(String(500))

    delivery: Mapped[Delivery] = relationship(back_populates="flags", lazy="noload")

    __table_args__ = (
        Index("ix_delivery_flags_open", "delivery_id", "status"),
        enum_check("flag_type", FlagType),
        enum_check("severity", FlagSeverity),
        enum_check("status", FlagStatus),
    )


class DeliveryReview(BaseModel):
    """Append-only (database trigger): every review decision, forever."""

    __tablename__ = "delivery_reviews"

    delivery_id: Mapped[UUID] = mapped_column(
        ForeignKey("deliveries.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    company_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    reviewer_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    reviewer_name: Mapped[str | None] = mapped_column(String(160))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    comments: Mapped[str | None] = mapped_column(String(1000))
    corrected_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    previous_status: Mapped[str] = mapped_column(String(30), nullable=False)
    new_status: Mapped[str] = mapped_column(String(30), nullable=False)

    __table_args__ = (enum_check("action", ReviewAction),)

"""Goods received notes (docs/02 §6).

A GRN is what turns an approved delivery into stock. Posting one is the moment
inventory moves (and, from Phase 4, the general ledger) — never before.
"""

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
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constraints import enum_check, non_negative
from app.core.db import BaseModel, CompanyModel, VersionMixin
from app.modules.grn.domain.enums import GrnStatus, InspectionResult


class Grn(CompanyModel, VersionMixin):
    __tablename__ = "grns"
    __audited__ = True

    grn_number: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=GrnStatus.DRAFT.value)

    # A GRN may exist without a delivery (counter purchase); a delivery may
    # exist without a GRN (rejected). Both links are therefore nullable.
    delivery_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("deliveries.id", ondelete="RESTRICT"), index=True
    )
    purchase_order_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="RESTRICT"), index=True
    )
    vendor_id: Mapped[UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    warehouse_id: Mapped[UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    received_date: Mapped[date] = mapped_column(Date, nullable=False)
    inspection_result: Mapped[str] = mapped_column(
        String(10), nullable=False, default=InspectionResult.PENDING.value
    )
    inspected_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    # Set when finance posts to the ledger (Phase 4); no foreign key until that
    # table exists.
    journal_entry_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    remarks: Mapped[str | None] = mapped_column(String(1000))

    items: Mapped[list[GrnItem]] = relationship(
        back_populates="grn",
        order_by="GrnItem.line_no",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "grn_number", name="uq_grns_number"),
        # One live GRN per delivery: a delivery is received once.
        Index(
            "uq_grns_live_delivery",
            "delivery_id",
            unique=True,
            postgresql_where="status <> 'CANCELLED' AND delivery_id IS NOT NULL",
        ),
        enum_check("status", GrnStatus),
        enum_check("inspection_result", InspectionResult),
        non_negative("gross_amount"),
        CheckConstraint(
            "status <> 'CANCELLED' OR cancel_reason IS NOT NULL", name="cancel_needs_reason"
        ),
    )


class GrnItem(BaseModel):
    __tablename__ = "grn_items"
    __audited__ = True

    grn_id: Mapped[UUID] = mapped_column(
        ForeignKey("grns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    po_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchase_order_items.id", ondelete="RESTRICT")
    )
    delivery_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("delivery_items.id", ondelete="RESTRICT")
    )
    material_id: Mapped[UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    unit_id: Mapped[UUID] = mapped_column(
        ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )
    ordered_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    delivered_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    accepted_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    rejected_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    rejection_reason: Mapped[str | None] = mapped_column(String(300))

    # Snapshotted from the delivery line: the price the load was counted at.
    rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    vendor_rate_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))

    # What went into stock: the accepted quantity in the material's base unit,
    # at cost per base unit, and the ledger row that recorded it.
    base_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    inventory_txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))

    batch_no: Mapped[str | None] = mapped_column(String(60))
    expiry_date: Mapped[date | None] = mapped_column(Date)

    grn: Mapped[Grn] = relationship(back_populates="items", lazy="noload")

    __table_args__ = (
        UniqueConstraint("grn_id", "line_no", name="uq_grn_items_line"),
        non_negative("delivered_quantity"),
        non_negative("accepted_quantity"),
        non_negative("rejected_quantity"),
        # What arrived is either taken or turned away: nothing goes missing
        # between the truck and the ledger.
        CheckConstraint(
            "accepted_quantity + rejected_quantity = delivered_quantity",
            name="accepted_plus_rejected_equals_delivered",
        ),
        CheckConstraint(
            "rejected_quantity = 0 OR rejection_reason IS NOT NULL", name="rejection_needs_reason"
        ),
    )

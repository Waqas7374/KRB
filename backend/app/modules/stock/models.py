"""Stock issues, transfers and adjustments (docs/02 §6).

Three documents, three ways stock moves other than a receipt. None of them
touches a balance itself: each posts through `inventory.ledger.post`, the one
door into the stock ledger, so the ledger's rules hold for all of them.
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
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constraints import enum_check, positive
from app.core.db import BaseModel, CompanyModel, VersionMixin
from app.modules.stock.domain.enums import (
    AdjustmentReason,
    AdjustmentStatus,
    IssuedToType,
    IssueStatus,
    TransferStatus,
)

# -----------------------------------------------------------------------------
# Issues
# -----------------------------------------------------------------------------


class StockIssue(CompanyModel, VersionMixin):
    """Material handed out of a store, to a person, a contractor or a job."""

    __tablename__ = "stock_issues"
    __audited__ = True

    issue_number: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=IssueStatus.DRAFT.value)
    warehouse_id: Mapped[UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Copied from the warehouse's site so the document is scoped like the stock.
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    issued_to_type: Mapped[str] = mapped_column(String(15), nullable=False)
    # Who or what it went to, as people know it. Employees and work orders have
    # no table of their own yet; this is the name on the slip.
    issued_to_name: Mapped[str] = mapped_column(String(160), nullable=False)
    purpose: Mapped[str] = mapped_column(String(300), nullable=False)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    remarks: Mapped[str | None] = mapped_column(String(1000))

    items: Mapped[list[StockIssueItem]] = relationship(
        back_populates="issue",
        order_by="StockIssueItem.line_no",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "issue_number", name="uq_stock_issues_number"),
        enum_check("status", IssueStatus),
        enum_check("issued_to_type", IssuedToType),
        CheckConstraint(
            "status <> 'CANCELLED' OR cancel_reason IS NOT NULL",
            name="cancel_needs_reason",
        ),
    )


class StockIssueItem(BaseModel):
    __tablename__ = "stock_issue_items"
    __audited__ = True

    issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("stock_issues.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    material_id: Mapped[UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # As entered (a site counts bags, the ledger holds tonnes).
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_id: Mapped[UUID] = mapped_column(
        ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )
    # Set when posted: what left, in the material's base unit, and what it cost.
    base_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    inventory_txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    remarks: Mapped[str | None] = mapped_column(String(300))

    issue: Mapped[StockIssue] = relationship(back_populates="items", lazy="noload")

    __table_args__ = (
        UniqueConstraint("issue_id", "line_no", name="uq_stock_issue_items_line"),
        positive("quantity"),
    )


# -----------------------------------------------------------------------------
# Transfers
# -----------------------------------------------------------------------------


class StockTransfer(CompanyModel, VersionMixin):
    """Stock moving from one store to another, possibly on another site.

    Two ledger rows per line, written at two different moments: out of the
    source when it is dispatched, into the destination when it is counted in.
    Between the two the goods are on a truck, and the destination shows them as
    "in transit" — not on hand, not lost.
    """

    __tablename__ = "stock_transfers"
    __audited__ = True

    transfer_number: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TransferStatus.DRAFT.value
    )
    from_warehouse_id: Mapped[UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    to_warehouse_id: Mapped[UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # `site_id` / `project_id` are the *source's*: they scope the document.
    # The destination's site is visible to that site's people as well.
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    to_project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT")
    )
    to_site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    transfer_date: Mapped[date] = mapped_column(Date, nullable=False)
    vehicle_number: Mapped[str | None] = mapped_column(String(30))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    remarks: Mapped[str | None] = mapped_column(String(1000))

    items: Mapped[list[StockTransferItem]] = relationship(
        back_populates="transfer",
        order_by="StockTransferItem.line_no",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "transfer_number", name="uq_stock_transfers_number"),
        enum_check("status", TransferStatus),
        CheckConstraint("from_warehouse_id <> to_warehouse_id", name="different_warehouses"),
        CheckConstraint(
            "status <> 'CANCELLED' OR cancel_reason IS NOT NULL",
            name="cancel_needs_reason",
        ),
    )


class StockTransferItem(BaseModel):
    __tablename__ = "stock_transfer_items"
    __audited__ = True

    transfer_id: Mapped[UUID] = mapped_column(
        ForeignKey("stock_transfers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    material_id: Mapped[UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_id: Mapped[UUID] = mapped_column(
        ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )
    # Set at dispatch: the base-unit quantity and the cost it left the source at
    # — the same cost it arrives at, so a transfer never changes a value.
    base_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    out_txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    in_txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    remarks: Mapped[str | None] = mapped_column(String(300))

    transfer: Mapped[StockTransfer] = relationship(back_populates="items", lazy="noload")

    __table_args__ = (
        UniqueConstraint("transfer_id", "line_no", name="uq_stock_transfer_items_line"),
        positive("quantity"),
    )


# -----------------------------------------------------------------------------
# Adjustments
# -----------------------------------------------------------------------------


class StockAdjustment(CompanyModel, VersionMixin):
    """A correction to what the books say is on the shelf.

    The most dangerous document in the system — it creates or destroys stock
    without a receipt or an issue behind it — so it always carries a reason and
    always goes through approval; the rules that route it live in the workflow,
    not here.
    """

    __tablename__ = "stock_adjustments"
    __audited__ = True

    adjustment_number: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AdjustmentStatus.DRAFT.value
    )
    warehouse_id: Mapped[UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reason_code: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_note: Mapped[str] = mapped_column(String(500), nullable=False)
    adjustment_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Set once, at submission; kept when the request is decided so the trail is
    # reachable from the document.
    approval_request_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(String(2000))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))

    items: Mapped[list[StockAdjustmentItem]] = relationship(
        back_populates="adjustment",
        order_by="StockAdjustmentItem.line_no",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "adjustment_number", name="uq_stock_adjustments_number"),
        enum_check("status", AdjustmentStatus),
        enum_check("reason_code", AdjustmentReason),
        # A posted adjustment went through an approval; there is no other way in.
        CheckConstraint(
            "status <> 'POSTED' OR approval_request_id IS NOT NULL",
            name="posted_needs_approval",
        ),
        CheckConstraint(
            "status <> 'CANCELLED' OR cancel_reason IS NOT NULL",
            name="cancel_needs_reason",
        ),
    )


class StockAdjustmentItem(BaseModel):
    __tablename__ = "stock_adjustment_items"
    __audited__ = True

    adjustment_id: Mapped[UUID] = mapped_column(
        ForeignKey("stock_adjustments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    material_id: Mapped[UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Signed, in the material's base unit: positive adds stock, negative removes
    # it. Counting in the ledger's own unit means a correction is never
    # ambiguous about what it changes.
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    # What an increase is valued at, fixed when the document is raised (the
    # current average unless a cost is given). A decrease leaves at the average
    # cost of the day it is posted, so it is recorded from the ledger instead.
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    value_delta: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    # What the books said was on hand when the document was raised: what the
    # person was looking at when they decided how much to correct.
    system_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    inventory_txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    remarks: Mapped[str | None] = mapped_column(String(300))

    adjustment: Mapped[StockAdjustment] = relationship(back_populates="items", lazy="noload")

    __table_args__ = (
        UniqueConstraint("adjustment_id", "line_no", name="uq_stock_adjustment_items_line"),
        CheckConstraint("quantity_delta <> 0", name="delta_not_zero"),
    )

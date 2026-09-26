"""The stock ledger and its cached balances (docs/02 §7).

`InventoryTransaction` is the truth: append-only (database trigger), never
updated, never deleted. Corrections are contra rows. `InventoryBalance` is a
projection of it, updated in the same transaction as each ledger row under a
row lock, and proved against the ledger every night (inventory.reconcile).
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
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constraints import enum_check, non_negative
from app.core.db import BaseModel, CompanyModel
from app.modules.inventory.domain.enums import TxnType


class InventoryTransaction(BaseModel):
    """Append-only (database trigger): one movement of one material at one warehouse."""

    __tablename__ = "inventory_transactions"

    company_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    warehouse_id: Mapped[UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    material_id: Mapped[UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    txn_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Always in the material's base unit: one ledger, one unit, no ambiguity.
    unit_id: Mapped[UUID] = mapped_column(
        ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )
    quantity_in: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    quantity_out: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=0)
    value_in: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    value_out: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    balance_quantity_after: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    balance_value_after: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)

    # What caused it: a GRN line, an issue, a transfer, an adjustment...
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    source_line_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    project_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), index=True)
    site_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), index=True)
    cost_center_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reversal_of_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    remarks: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        Index("ix_inventory_transactions_stock", "warehouse_id", "material_id", "posted_at"),
        enum_check("txn_type", TxnType),
        non_negative("quantity_in"),
        non_negative("quantity_out"),
        non_negative("value_in"),
        non_negative("value_out"),
        non_negative("balance_quantity_after"),
        # A row moves stock one way: in or out, never both, never neither.
        CheckConstraint(
            "(quantity_in > 0 AND quantity_out = 0) OR (quantity_out > 0 AND quantity_in = 0)",
            name="moves_one_way",
        ),
    )


class InventoryBalance(CompanyModel):
    """Stock on hand for one material at one warehouse: a cached projection of
    the ledger, valued at weighted average cost."""

    __tablename__ = "inventory_balances"

    warehouse_id: Mapped[UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    material_id: Mapped[UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Copied from the warehouse's site so balances are scoped like everything else.
    project_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), index=True)
    site_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), index=True)
    quantity_on_hand: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    quantity_reserved: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    quantity_in_transit: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    average_cost: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=0)
    total_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    last_transaction_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    last_movement_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("warehouse_id", "material_id", name="uq_inventory_balances_stock"),
        # The database, not the caller, refuses to sell what is not there.
        non_negative("quantity_on_hand"),
        non_negative("total_value"),
    )

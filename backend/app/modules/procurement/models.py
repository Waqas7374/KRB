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
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constraints import enum_check, non_negative, positive
from app.core.db import BaseModel, CompanyModel, VersionMixin
from app.modules.procurement.domain.enums import PurchaseRequestPriority, PurchaseRequestStatus


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

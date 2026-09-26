"""Vendor rates (docs/02 §5, docs/05 §4)."""

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
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constraints import enum_check, non_negative
from app.core.db import BaseModel, CompanyModel, VersionMixin
from app.modules.rates.domain.enums import RateSource, RateStatus

_NIL = "'00000000-0000-0000-0000-000000000000'::uuid"


class VendorRate(CompanyModel, VersionMixin):
    """One period of one vendor's price for one material in one unit.

    The rate value is never edited. `effective_to` is set once, when a later
    period supersedes this one; `notes` may be corrected. Everything else about
    a change lives in `VendorRateHistory`.
    """

    __tablename__ = "vendor_rates"
    __audited__ = True
    # Not scope_company_wide: rates carry project and site, so a site-scoped
    # reader sees the rates for their site (and only those).

    vendor_id: Mapped[UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    material_id: Mapped[UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    unit_id: Mapped[UUID] = mapped_column(
        ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="PKR")
    # Both null: company-wide. Project only: that project. Site set: that site.
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    site_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sites.id", ondelete="RESTRICT"), index=True
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RateStatus.PENDING_APPROVAL.value
    )
    source: Mapped[str] = mapped_column(String(12), nullable=False, default=RateSource.MANUAL.value)
    reason: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(String(500))

    # What this change was measured against, fixed at submission so the
    # approver sees the same figure the workflow routed on.
    previous_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    change_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 2))
    supersedes_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))

    requested_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(String(2000))
    approval_request_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))

    __table_args__ = (
        Index("ix_vendor_rates_lookup", "company_id", "vendor_id", "material_id", "status"),
        enum_check("status", RateStatus),
        enum_check("source", RateSource),
        non_negative("rate"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="effective_period_valid"
        ),
        # No two approved periods for the same vendor, material, unit and scope
        # may overlap (docs/02): the database, not the code, guarantees a date
        # resolves to at most one rate per scope.
        ExcludeConstraint(
            ("vendor_id", "="),
            ("material_id", "="),
            ("unit_id", "="),
            (text(f"coalesce(project_id, {_NIL})"), "="),
            (text(f"coalesce(site_id, {_NIL})"), "="),
            (text("daterange(effective_from, effective_to, '[]')"), "&&"),
            where=text("status = 'ACTIVE'"),
            using="gist",
            name="ex_vendor_rates_no_overlap",
        ),
    )


class VendorRateHistory(BaseModel):
    """Append-only (database trigger): every rate change, with who and why.

    The application has no way to edit or delete a row here, so "why did the
    price of crush change in August" always has an answer.
    """

    __tablename__ = "vendor_rate_history"

    vendor_rate_id: Mapped[UUID] = mapped_column(
        ForeignKey("vendor_rates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    vendor_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    material_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    superseded_rate_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    old_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    new_rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    change_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 2))
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    changed_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    approval_request_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)

    __table_args__ = (
        CheckConstraint("new_rate >= 0", name="new_rate_non_negative"),
        Index("ix_vendor_rate_history_lookup", "company_id", "vendor_id", "material_id"),
    )

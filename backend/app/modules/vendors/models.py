"""Vendors: suppliers, contractors and service providers.

Two deliberate choices:

* **Bank accounts are a separate table behind their own permission.** Changing
  where a vendor's money goes is the highest-value fraud in procurement, so it
  is separated from ordinary vendor editing and always audited with masked
  values.
* **No composite vendor score** (§11, decision D-13). `vendor_performance_facts`
  holds measured facts — deliveries, on-time count, rejections, variance. A
  single "rating" number would be a judgement dressed as data, and the brief
  forbids it unless the methodology is configurable.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constraints import enum_check, non_negative
from app.core.db import CompanyModel, MasterDataModel
from app.core.sync import SyncSeqMixin
from app.modules.vendors.domain.enums import VendorStatus, VendorType


class Vendor(MasterDataModel, SyncSeqMixin):
    __tablename__ = "vendors"
    # Company-wide reference data: see core/scoping.py.
    __scope_company_wide__ = True

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(String(200))
    vendor_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default=VendorType.MATERIAL_SUPPLIER.value
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=VendorStatus.DRAFT.value
    )

    # --- Pakistan tax identifiers (decision Q1) ------------------------------
    ntn: Mapped[str | None] = mapped_column(String(20), comment="National Tax Number")
    strn: Mapped[str | None] = mapped_column(String(20), comment="Sales Tax Registration Number")
    cnic: Mapped[str | None] = mapped_column(
        String(20), comment="For a sole proprietor with no NTN"
    )
    is_filer: Mapped[bool | None] = mapped_column(
        Boolean, comment="FBR active-taxpayer status; drives the withholding rate"
    )
    withholding_exempt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    withholding_certificate_ref: Mapped[str | None] = mapped_column(String(80))

    # --- Commercial terms ----------------------------------------------------
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="PKR")

    # --- Contact -------------------------------------------------------------
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(160))
    website: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # --- Lifecycle -----------------------------------------------------------
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspension_reason: Mapped[str | None] = mapped_column(String(500))

    notes: Mapped[str | None] = mapped_column(String(2000))

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_vendors_company_id_code"),
        enum_check("status", VendorStatus),
        enum_check("vendor_type", VendorType),
        CheckConstraint("payment_terms_days >= 0", name="payment_terms_non_negative"),
        CheckConstraint(
            "credit_limit IS NULL OR credit_limit >= 0", name="credit_limit_non_negative"
        ),
        # A suspended or blacklisted vendor must say why.
        CheckConstraint(
            "status NOT IN ('SUSPENDED', 'BLACKLISTED') OR suspension_reason IS NOT NULL",
            name="suspension_requires_reason",
        ),
        Index("ix_vendors_company_id_status", "company_id", "status"),
        Index(
            "ix_vendors_legal_name_trgm",
            "legal_name",
            postgresql_using="gin",
            postgresql_ops={"legal_name": "gin_trgm_ops"},
        ),
    )

    contacts: Mapped[list[VendorContact]] = relationship(
        back_populates="vendor", lazy="selectin", cascade="all, delete-orphan"
    )

    @property
    def is_tradeable(self) -> bool:
        """Whether a purchase order may be raised against this vendor."""
        return self.status == VendorStatus.ACTIVE.value and self.deleted_at is None

    @property
    def display_name(self) -> str:
        return self.trade_name or self.legal_name


class VendorContact(MasterDataModel):
    __tablename__ = "vendor_contacts"
    # Company-wide reference data: see core/scoping.py.
    __scope_company_wide__ = True

    vendor_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    designation: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(32))
    alternate_phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(160))
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        # At most one primary contact per vendor.
        Index(
            "uq_vendor_contacts_primary",
            "vendor_id",
            unique=True,
            postgresql_where="is_primary AND deleted_at IS NULL",
        ),
        CheckConstraint("phone IS NOT NULL OR email IS NOT NULL", name="phone_or_email_required"),
    )

    vendor: Mapped[Vendor] = relationship(back_populates="contacts", lazy="noload")


class VendorBankAccount(MasterDataModel):
    """Where a vendor's money goes.

    Behind `vendors.manage_bank_details`, audited with masked values, and
    `verified_at` records that a human checked the details against a document
    rather than typing what an email said.
    """

    __tablename__ = "vendor_bank_accounts"
    __audit_exclude__ = ()

    vendor_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_title: Mapped[str] = mapped_column(String(200), nullable=False)
    account_no: Mapped[str | None] = mapped_column(String(40))
    iban: Mapped[str | None] = mapped_column(String(34))
    bank_name: Mapped[str] = mapped_column(String(160), nullable=False)
    branch_name: Mapped[str | None] = mapped_column(String(160))
    branch_code: Mapped[str | None] = mapped_column(String(20))
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="PKR")
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    verification_note: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        CheckConstraint(
            "account_no IS NOT NULL OR iban IS NOT NULL", name="account_no_or_iban_required"
        ),
        Index(
            "uq_vendor_bank_accounts_primary",
            "vendor_id",
            unique=True,
            postgresql_where="is_primary AND deleted_at IS NULL",
        ),
    )

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None

    @property
    def masked_account(self) -> str:
        """What non-privileged views show."""
        source = self.iban or self.account_no or ""
        return f"****{source[-4:]}" if len(source) > 4 else "****"


class VendorMaterial(MasterDataModel):
    """What a vendor supplies, and how they behave when supplying it."""

    __tablename__ = "vendor_materials"
    # Company-wide reference data: see core/scoping.py.
    __scope_company_wide__ = True

    vendor_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    material_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("materials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lead_time_days: Mapped[int | None] = mapped_column(SmallInteger)
    min_order_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    vendor_material_code: Mapped[str | None] = mapped_column(
        String(60), comment="The vendor's own code, for matching their challans"
    )
    is_preferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint(
            "vendor_id", "material_id", name="uq_vendor_materials_vendor_id_material_id"
        ),
        CheckConstraint(
            "min_order_quantity IS NULL OR min_order_quantity > 0",
            name="min_order_quantity_positive",
        ),
    )


class VendorPerformanceFact(CompanyModel):
    """Measured facts per vendor per month. Rebuilt nightly.

    Facts only — no score (§11). A consumer can rank on whichever column
    matters to them, and the methodology stays visible.
    """

    __tablename__ = "vendor_performance_facts"

    vendor_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    period_month: Mapped[date] = mapped_column(
        Date, nullable=False, comment="First day of the month the facts cover"
    )

    deliveries_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    on_time_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    late_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flagged_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    ordered_quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    delivered_quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    accepted_quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    purchase_value: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )

    quality_incidents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "vendor_id", "period_month", name="uq_vendor_performance_facts_vendor_id_period"
        ),
        non_negative("deliveries_count"),
        non_negative("rejected_count"),
        CheckConstraint(
            "on_time_count + late_count <= deliveries_count", name="timing_counts_consistent"
        ),
        Index("ix_vendor_performance_facts_period", "company_id", "period_month"),
    )


__all__ = [
    "Vendor",
    "VendorBankAccount",
    "VendorContact",
    "VendorMaterial",
    "VendorPerformanceFact",
]

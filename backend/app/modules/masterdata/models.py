"""Master data: units, unit conversions, material categories, materials,
truck types and warehouses.

The important table here is `unit_conversions`. No conversion factor is
hard-coded anywhere in this codebase (§20): `1 tonne = X cft` is a row an
administrator maintains, effective-dated, and scoped from most specific to
least — material+vendor, then material, then global. Every document that
applies a factor snapshots it, so correcting a wrong factor next month changes
future documents only and never restates a posted one.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
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

from app.core.constraints import enum_check, positive
from app.core.db import MasterDataModel
from app.core.sync import SyncSeqMixin
from app.modules.masterdata.domain.enums import (
    CalibrationStatus,
    ConversionScope,
    MaterialTracking,
    UnitDimension,
    WarehouseType,
)


class Unit(MasterDataModel, SyncSeqMixin):
    """A unit of measure.

    `dimension` is what makes an impossible conversion detectable: tonnes to
    cubic feet crosses MASS to VOLUME, which is only meaningful for a specific
    material at a known density — hence the material-scoped conversion rows.
    """

    __tablename__ = "units"
    # Company-wide reference data: see core/scoping.py.
    __scope_company_wide__ = True

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(12))
    dimension: Mapped[str] = mapped_column(String(20), nullable=False)
    # Decimal places this unit is measured to; 0 for bags and pieces.
    precision: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=4)

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_units_company_id_code"),
        enum_check("dimension", UnitDimension),
        CheckConstraint("precision BETWEEN 0 AND 6", name="precision_range"),
    )

    def __str__(self) -> str:
        return self.code


class UnitConversion(MasterDataModel):
    """An effective-dated conversion factor: 1 `from_unit` = `factor` `to_unit`.

    Append-only in practice: changing a factor closes the current row with
    `effective_to` and inserts a new one, so a document posted last month can
    still be recomputed with the factor that was actually used.
    """

    __tablename__ = "unit_conversions"
    # Company-wide reference data: see core/scoping.py.
    __scope_company_wide__ = True
    __audited__ = True

    from_unit_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )
    to_unit_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )
    factor: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)

    scope_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ConversionScope.GLOBAL.value
    )
    material_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("materials.id", ondelete="CASCADE"), index=True
    )
    vendor_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), index=True
    )

    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)

    # Why the factor is what it is — "bulk density 1.55 t/m3, weighbridge
    # average Aug 2026". Without this, nobody can defend the number later.
    basis_note: Mapped[str | None] = mapped_column(String(500))
    supersedes_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("unit_conversions.id", ondelete="SET NULL")
    )

    __table_args__ = (
        positive("factor"),
        enum_check("scope_type", ConversionScope),
        CheckConstraint("from_unit_id <> to_unit_id", name="units_differ"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_dates_ordered",
        ),
        # The scope columns must match the declared scope, so resolution cannot
        # be confused by a MATERIAL-scoped row with no material.
        CheckConstraint(
            "(scope_type = 'GLOBAL' AND material_id IS NULL AND vendor_id IS NULL) "
            "OR (scope_type = 'MATERIAL' AND material_id IS NOT NULL AND vendor_id IS NULL) "
            "OR (scope_type = 'VENDOR' AND vendor_id IS NOT NULL AND material_id IS NULL) "
            "OR (scope_type = 'MATERIAL_VENDOR' "
            "    AND material_id IS NOT NULL AND vendor_id IS NOT NULL)",
            name="scope_columns_match_scope_type",
        ),
        # The resolution query: narrow by pair and scope, newest effective first.
        Index(
            "ix_unit_conversions_resolution",
            "company_id",
            "from_unit_id",
            "to_unit_id",
            "scope_type",
            "effective_from",
        ),
        Index("ix_unit_conversions_material_scope", "material_id", "vendor_id"),
    )

    from_unit: Mapped[Unit] = relationship(foreign_keys=[from_unit_id], lazy="joined")
    to_unit: Mapped[Unit] = relationship(foreign_keys=[to_unit_id], lazy="joined")

    @property
    def is_current(self) -> bool:
        return self.effective_to is None


class MaterialCategory(MasterDataModel):
    """Aggregates, Sand, Cement, Steel, Concrete, Pipes, Electrical (§9)."""

    __tablename__ = "material_categories"
    # Company-wide reference data: see core/scoping.py.
    __scope_company_wide__ = True

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("material_categories.id", ondelete="RESTRICT"), index=True
    )
    description: Mapped[str | None] = mapped_column(String(500))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_material_categories_company_id_code"),
        CheckConstraint("parent_id <> id", name="no_self_parent"),
    )


class Material(MasterDataModel, SyncSeqMixin):
    __tablename__ = "materials"
    # Company-wide reference data: see core/scoping.py.
    __scope_company_wide__ = True

    sku: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    category_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("material_categories.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    base_unit_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )
    # Present from day one so batch tracking for cement or steel is additive
    # rather than a migration of the whole stock ledger.
    tracking_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MaterialTracking.QUANTITY.value
    )

    min_stock: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    max_stock: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    reorder_level: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))

    # Indicative planning rate. The rate a delivery is priced at comes from
    # `vendor_rates`, never from here.
    standard_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    standard_rate_unit_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("units.id", ondelete="RESTRICT")
    )

    hs_code: Mapped[str | None] = mapped_column(String(20))
    default_tax_code: Mapped[str | None] = mapped_column(String(20))

    is_stockable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_purchasable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Free-form specification: grade, size, mix, density.
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("company_id", "sku", name="uq_materials_company_id_sku"),
        enum_check("tracking_type", MaterialTracking),
        CheckConstraint(
            "max_stock IS NULL OR min_stock IS NULL OR max_stock >= min_stock",
            name="stock_bounds_ordered",
        ),
        CheckConstraint("min_stock IS NULL OR min_stock >= 0", name="min_stock_non_negative"),
        CheckConstraint(
            "standard_rate IS NULL OR standard_rate >= 0", name="standard_rate_non_negative"
        ),
        Index("ix_materials_company_id_category_id", "company_id", "category_id"),
        Index(
            "ix_materials_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index(
            "ix_materials_sku_trgm",
            "sku",
            postgresql_using="gin",
            postgresql_ops={"sku": "gin_trgm_ops"},
        ),
    )

    category: Mapped[MaterialCategory] = relationship(lazy="joined")
    base_unit: Mapped[Unit] = relationship(foreign_keys=[base_unit_id], lazy="joined")
    alternate_units: Mapped[list[MaterialUnit]] = relationship(
        back_populates="material", lazy="selectin", cascade="all, delete-orphan"
    )


class MaterialUnit(MasterDataModel):
    """An alternate unit a material may be transacted in (§9).

    Crush is stocked in tonnes but bought by the cubic foot; cement is both
    stocked and bought in bags. Listing the permitted units stops someone
    recording a delivery in a unit nobody has a conversion for.
    """

    __tablename__ = "material_units"
    # Company-wide reference data: see core/scoping.py.
    __scope_company_wide__ = True

    material_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("materials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    unit_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )
    is_purchase_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_issue_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_capture_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="Pre-selected on the mobile delivery form"
    )

    __table_args__ = (
        UniqueConstraint("material_id", "unit_id", name="uq_material_units_material_id_unit_id"),
        # Only one default of each kind per material.
        Index(
            "uq_material_units_purchase_default",
            "material_id",
            unique=True,
            postgresql_where="is_purchase_default",
        ),
        Index(
            "uq_material_units_capture_default",
            "material_id",
            unique=True,
            postgresql_where="is_capture_default",
        ),
    )

    material: Mapped[Material] = relationship(back_populates="alternate_units", lazy="noload")
    unit: Mapped[Unit] = relationship(lazy="joined")


class TruckType(MasterDataModel, SyncSeqMixin):
    """Vehicle classes and their legal / practical maximum load.

    `default_max_tonnage` seeds the TONNAGE_MAX business rule; the rule store is
    still the authority, because a site may legitimately cap lower (§16, §44).
    """

    __tablename__ = "truck_types"
    # Company-wide reference data: see core/scoping.py.
    __scope_company_wide__ = True

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    axle_count: Mapped[int | None] = mapped_column(SmallInteger)
    default_max_tonnage: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    typical_volume_cft: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    description: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_truck_types_company_id_code"),
        positive("default_max_tonnage"),
    )


class Warehouse(MasterDataModel):
    """Where stock physically sits.

    Stock is held at warehouse granularity, never against a site directly, so a
    site with a cement godown and an open aggregate yard can be counted
    separately.
    """

    __tablename__ = "warehouses"

    site_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    warehouse_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=WarehouseType.SITE_STORE.value
    )
    keeper_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # Default destination for goods received at this site.
    is_default_receiving: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    capacity_note: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_warehouses_company_id_code"),
        enum_check("warehouse_type", WarehouseType),
        Index(
            "uq_warehouses_default_receiving",
            "site_id",
            unique=True,
            postgresql_where="is_default_receiving",
        ),
    )


class UnitConversionCalibration(MasterDataModel):
    """One weighbridge reading, logged towards deriving a conversion factor.

    §20 forbids a hard-coded factor; it says nothing about where a correct one
    comes from. This table is that answer: an administrator records real
    (weight, volume) pairs from actual truck loads rather than typing a number
    from memory, reviews the spread across several readings, and only then
    confirms a factor — which becomes an ordinary effective-dated
    `unit_conversions` row via `applied_conversion_id`.

    Readings are never deleted, including discarded ones: a discarded reading
    is itself evidence of what went wrong with a particular truck or day.
    """

    __tablename__ = "unit_conversion_calibrations"
    __audited__ = True

    material_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("materials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # A calibration may be vendor-specific (this supplier's trucks run heavy)
    # or general for the material.
    vendor_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), index=True
    )

    from_unit_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )
    to_unit_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )

    # The two sides of one physical measurement — e.g. weighbridge weight in
    # TON and a measured/dumped volume in CFT for the same truckload.
    source_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    target_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    # Denormalised target_quantity / source_quantity, stored so the reading is
    # self-describing without recomputing it, and so a changed formula later
    # cannot silently reinterpret old readings.
    implied_factor: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)

    truck_number: Mapped[str | None] = mapped_column(String(20))
    weighbridge_ref: Mapped[str | None] = mapped_column(
        String(80), comment="Weighbridge slip / docket number, for traceability"
    )
    recorded_at: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500))

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CalibrationStatus.PENDING.value
    )
    applied_conversion_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("unit_conversions.id", ondelete="SET NULL")
    )
    discard_reason: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        positive("source_quantity"),
        positive("target_quantity"),
        positive("implied_factor"),
        enum_check("status", CalibrationStatus),
        CheckConstraint(
            "status <> 'DISCARDED' OR discard_reason IS NOT NULL",
            name="discard_requires_reason",
        ),
        CheckConstraint("from_unit_id <> to_unit_id", name="units_differ"),
        Index(
            "ix_calibrations_pending",
            "company_id",
            "material_id",
            "vendor_id",
            "from_unit_id",
            "to_unit_id",
            postgresql_where="status = 'PENDING'",
        ),
    )


__all__ = [
    "Material",
    "MaterialCategory",
    "MaterialUnit",
    "TruckType",
    "Unit",
    "UnitConversion",
    "UnitConversionCalibration",
    "Warehouse",
]

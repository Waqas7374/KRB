"""Master-data request and response schemas."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.masterdata.domain.enums import (
    CalibrationStatus,
    ConversionScope,
    MaterialTracking,
    UnitDimension,
    WarehouseType,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


Code = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Z0-9][A-Z0-9\-_/.]*$")]

# -----------------------------------------------------------------------------
# Units
# -----------------------------------------------------------------------------


class UnitCreate(ApiModel):
    code: Code
    name: Annotated[str, Field(min_length=1, max_length=80)]
    symbol: Annotated[str | None, Field(max_length=12)] = None
    dimension: UnitDimension
    precision: Annotated[int, Field(ge=0, le=6)] = 4


class UnitUpdate(ApiModel):
    name: Annotated[str | None, Field(min_length=1, max_length=80)] = None
    symbol: Annotated[str | None, Field(max_length=12)] = None
    precision: Annotated[int | None, Field(ge=0, le=6)] = None


class UnitRead(ApiModel):
    id: UUID
    code: str
    name: str
    symbol: str | None
    dimension: str
    precision: int
    version: int
    created_at: datetime
    updated_at: datetime


# -----------------------------------------------------------------------------
# Unit conversions
# -----------------------------------------------------------------------------


class ConversionCreate(ApiModel):
    from_unit_id: UUID
    to_unit_id: UUID
    factor: Annotated[Decimal, Field(gt=0, max_digits=24, decimal_places=12)]
    scope_type: ConversionScope = ConversionScope.GLOBAL
    material_id: UUID | None = None
    vendor_id: UUID | None = None
    effective_from: date | None = None
    basis_note: Annotated[str | None, Field(max_length=500)] = None

    @model_validator(mode="after")
    def _scope_matches_ids(self) -> ConversionCreate:
        needs_material = self.scope_type in (
            ConversionScope.MATERIAL,
            ConversionScope.MATERIAL_VENDOR,
        )
        needs_vendor = self.scope_type in (
            ConversionScope.VENDOR,
            ConversionScope.MATERIAL_VENDOR,
        )
        if needs_material and self.material_id is None:
            raise ValueError(f"scope_type {self.scope_type.value} requires material_id")
        if needs_vendor and self.vendor_id is None:
            raise ValueError(f"scope_type {self.scope_type.value} requires vendor_id")
        if self.scope_type is ConversionScope.GLOBAL and (self.material_id or self.vendor_id):
            raise ValueError("A GLOBAL conversion must not carry material_id or vendor_id")
        if self.scope_type is ConversionScope.MATERIAL and self.vendor_id is not None:
            raise ValueError("A MATERIAL-scoped conversion must not carry vendor_id")
        if self.scope_type is ConversionScope.VENDOR and self.material_id is not None:
            raise ValueError("A VENDOR-scoped conversion must not carry material_id")
        return self


class ConversionRead(ApiModel):
    id: UUID
    from_unit_id: UUID
    from_unit_code: str | None = None
    to_unit_id: UUID
    to_unit_code: str | None = None
    factor: Decimal
    scope_type: str
    material_id: UUID | None
    vendor_id: UUID | None
    effective_from: date
    effective_to: date | None
    is_current: bool
    basis_note: str | None
    supersedes_id: UUID | None
    version: int
    created_at: datetime


class ConversionResolveRequest(ApiModel):
    from_unit_id: UUID
    to_unit_id: UUID
    material_id: UUID | None = None
    vendor_id: UUID | None = None
    at: date | None = None
    quantity: Annotated[Decimal | None, Field(ge=0, max_digits=18, decimal_places=4)] = None


class ConversionResolveResponse(ApiModel):
    factor: Decimal
    conversion_id: UUID | None
    scope: str
    inverted: bool
    via_unit_code: str | None
    converted_quantity: Decimal | None = None


# -----------------------------------------------------------------------------
# Calibration
# -----------------------------------------------------------------------------


class CalibrationReadingCreate(ApiModel):
    """One weighbridge reading: what a truck weighed and what it measured as."""

    material_id: UUID
    vendor_id: UUID | None = None
    from_unit_id: UUID
    to_unit_id: UUID
    source_quantity: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
    target_quantity: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
    recorded_at: date
    truck_number: Annotated[str | None, Field(max_length=20)] = None
    weighbridge_ref: Annotated[str | None, Field(max_length=80)] = None
    notes: Annotated[str | None, Field(max_length=500)] = None


class CalibrationReadingRead(ApiModel):
    id: UUID
    material_id: UUID
    vendor_id: UUID | None
    from_unit_id: UUID
    to_unit_id: UUID
    source_quantity: Decimal
    target_quantity: Decimal
    implied_factor: Decimal
    truck_number: str | None
    weighbridge_ref: str | None
    recorded_at: date
    notes: str | None
    status: CalibrationStatus
    applied_conversion_id: UUID | None
    discard_reason: str | None
    created_at: datetime


class CalibrationDiscard(ApiModel):
    reason: Annotated[str, Field(min_length=3, max_length=300)]


class CalibrationStatsResponse(ApiModel):
    count: int
    average_factor: Decimal | None
    median_factor: Decimal | None
    min_factor: Decimal | None
    max_factor: Decimal | None
    spread_pct: Decimal | None
    readings: list[CalibrationReadingRead]


class CalibrationConfirm(ApiModel):
    """Confirm a factor derived from named readings.

    `factor` is supplied by the admin — typically the average or median from
    the stats endpoint, but never applied automatically: a person reviews the
    spread and decides.
    """

    material_id: UUID
    vendor_id: UUID | None = None
    from_unit_id: UUID
    to_unit_id: UUID
    factor: Annotated[Decimal, Field(gt=0, max_digits=24, decimal_places=12)]
    reading_ids: Annotated[list[UUID], Field(min_length=1)]
    effective_from: date
    basis_note: Annotated[str | None, Field(max_length=500)] = None


# -----------------------------------------------------------------------------
# Material categories & materials
# -----------------------------------------------------------------------------


class MaterialCategoryCreate(ApiModel):
    code: Code
    name: Annotated[str, Field(min_length=1, max_length=120)]
    parent_id: UUID | None = None
    description: Annotated[str | None, Field(max_length=500)] = None
    sequence: int = 0


class MaterialCategoryRead(ApiModel):
    id: UUID
    code: str
    name: str
    parent_id: UUID | None
    description: str | None
    sequence: int
    version: int


class MaterialAlternateUnitIn(ApiModel):
    unit_id: UUID
    is_purchase_default: bool = False
    is_issue_default: bool = False
    is_capture_default: bool = False


class MaterialCreate(ApiModel):
    sku: Code
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str | None, Field(max_length=1000)] = None
    category_id: UUID
    base_unit_id: UUID
    tracking_type: MaterialTracking = MaterialTracking.QUANTITY
    min_stock: Annotated[Decimal | None, Field(ge=0, max_digits=18, decimal_places=4)] = None
    max_stock: Annotated[Decimal | None, Field(ge=0, max_digits=18, decimal_places=4)] = None
    reorder_level: Annotated[Decimal | None, Field(ge=0, max_digits=18, decimal_places=4)] = None
    standard_rate: Annotated[Decimal | None, Field(ge=0, max_digits=18, decimal_places=6)] = None
    standard_rate_unit_id: UUID | None = None
    hs_code: Annotated[str | None, Field(max_length=20)] = None
    default_tax_code: Annotated[str | None, Field(max_length=20)] = None
    is_stockable: bool = True
    is_purchasable: bool = True
    attributes: dict[str, Any] = Field(default_factory=dict)
    alternate_units: list[MaterialAlternateUnitIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _stock_bounds(self) -> MaterialCreate:
        if (
            self.min_stock is not None
            and self.max_stock is not None
            and self.max_stock < self.min_stock
        ):
            raise ValueError("max_stock cannot be less than min_stock")
        return self


class MaterialUpdate(ApiModel):
    name: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    description: Annotated[str | None, Field(max_length=1000)] = None
    category_id: UUID | None = None
    min_stock: Annotated[Decimal | None, Field(ge=0, max_digits=18, decimal_places=4)] = None
    max_stock: Annotated[Decimal | None, Field(ge=0, max_digits=18, decimal_places=4)] = None
    reorder_level: Annotated[Decimal | None, Field(ge=0, max_digits=18, decimal_places=4)] = None
    standard_rate: Annotated[Decimal | None, Field(ge=0, max_digits=18, decimal_places=6)] = None
    standard_rate_unit_id: UUID | None = None
    hs_code: Annotated[str | None, Field(max_length=20)] = None
    is_stockable: bool | None = None
    is_purchasable: bool | None = None
    attributes: dict[str, Any] | None = None


class MaterialAlternateUnitRead(ApiModel):
    id: UUID
    unit_id: UUID
    is_purchase_default: bool
    is_issue_default: bool
    is_capture_default: bool


class MaterialRead(ApiModel):
    id: UUID
    sku: str
    name: str
    description: str | None
    category_id: UUID
    base_unit_id: UUID
    tracking_type: str
    min_stock: Decimal | None
    max_stock: Decimal | None
    reorder_level: Decimal | None
    standard_rate: Decimal | None
    standard_rate_unit_id: UUID | None
    hs_code: str | None
    is_stockable: bool
    is_purchasable: bool
    attributes: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime


class MaterialDetail(MaterialRead):
    alternate_units: list[MaterialAlternateUnitRead] = Field(default_factory=list)


class MaterialListItem(ApiModel):
    id: UUID
    sku: str
    name: str
    category_id: UUID
    category_name: str | None = None
    base_unit_id: UUID
    base_unit_code: str | None = None
    standard_rate: Decimal | None
    is_stockable: bool
    is_purchasable: bool
    updated_at: datetime


# -----------------------------------------------------------------------------
# Truck types & warehouses
# -----------------------------------------------------------------------------


class TruckTypeCreate(ApiModel):
    code: Code
    name: Annotated[str, Field(min_length=1, max_length=120)]
    axle_count: Annotated[int | None, Field(ge=2, le=12)] = None
    default_max_tonnage: Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=3)]
    typical_volume_cft: Annotated[Decimal | None, Field(gt=0, max_digits=12, decimal_places=3)] = (
        None
    )
    description: Annotated[str | None, Field(max_length=300)] = None


class TruckTypeRead(ApiModel):
    id: UUID
    code: str
    name: str
    axle_count: int | None
    default_max_tonnage: Decimal
    typical_volume_cft: Decimal | None
    description: str | None
    version: int


class WarehouseCreate(ApiModel):
    code: Code
    name: Annotated[str, Field(min_length=1, max_length=160)]
    site_id: UUID
    warehouse_type: WarehouseType = WarehouseType.SITE_STORE
    keeper_user_id: UUID | None = None
    is_default_receiving: bool = False
    capacity_note: Annotated[str | None, Field(max_length=300)] = None


class WarehouseRead(ApiModel):
    id: UUID
    code: str
    name: str
    site_id: UUID
    warehouse_type: str
    keeper_user_id: UUID | None
    is_default_receiving: bool
    capacity_note: str | None
    version: int
    created_at: datetime


def _rebuild() -> None:
    MaterialDetail.model_rebuild()


_rebuild()

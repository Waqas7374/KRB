"""Master-data endpoints: units, conversions, calibration, materials,
categories, truck types, warehouses.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Access, PageDep, SessionDep, UowDep, require
from app.core.pagination import Page
from app.modules.masterdata.domain.enums import CalibrationStatus, ConversionScope
from app.modules.masterdata.models import (
    Material,
    MaterialCategory,
    MaterialUnit,
    Unit,
    UnitConversion,
)
from app.modules.masterdata.schemas import (
    CalibrationConfirm,
    CalibrationDiscard,
    CalibrationReadingCreate,
    CalibrationReadingRead,
    CalibrationStatsResponse,
    ConversionCreate,
    ConversionRead,
    ConversionResolveRequest,
    ConversionResolveResponse,
    MaterialAlternateUnitIn,
    MaterialAlternateUnitRead,
    MaterialCategoryCreate,
    MaterialCategoryRead,
    MaterialCreate,
    MaterialDetail,
    MaterialListItem,
    MaterialRead,
    MaterialUpdate,
    TruckTypeCreate,
    TruckTypeRead,
    UnitCreate,
    UnitRead,
    UnitUpdate,
    WarehouseCreate,
    WarehouseRead,
)
from app.modules.masterdata.services import calibration as calibration_service
from app.modules.masterdata.services import materials_service, units_service, warehouse_service
from app.modules.masterdata.services.conversion import UnitConverter

router = APIRouter()

# -----------------------------------------------------------------------------
# Units
# -----------------------------------------------------------------------------

units = APIRouter(prefix="/units", tags=["units"])


@units.get("", response_model=Page[UnitRead], dependencies=[require("units.view")])
async def list_units(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: Annotated[str | None, Query()] = None,
) -> Page[UnitRead]:
    repo = units_service.unit_repository(session)
    rows, total = await repo.list(ctx, "units.view", page=page, search=q)
    return Page[UnitRead].of([UnitRead.model_validate(r) for r in rows], params=page, total=total)


@units.post(
    "",
    response_model=UnitRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("units.manage")],
)
async def create_unit(payload: UnitCreate, ctx: Access, uow: UowDep) -> UnitRead:
    data = payload.model_dump()
    data["dimension"] = payload.dimension.value
    unit = await units_service.create_unit(uow.session, ctx, payload=data)
    return UnitRead.model_validate(unit)


@units.patch("/{unit_id}", response_model=UnitRead, dependencies=[require("units.manage")])
async def update_unit(
    unit_id: UUID,
    payload: UnitUpdate,
    ctx: Access,
    uow: UowDep,
    if_match: Annotated[int | None, Header(alias="If-Match")] = None,
) -> UnitRead:
    unit = await units_service.update_unit(
        uow.session,
        ctx,
        unit_id=unit_id,
        changes=payload.model_dump(exclude_unset=True),
        expected_version=if_match,
    )
    return UnitRead.model_validate(unit)


# -----------------------------------------------------------------------------
# Conversions
# -----------------------------------------------------------------------------

conversions = APIRouter(prefix="/unit-conversions", tags=["units"])


@conversions.get("", response_model=list[ConversionRead], dependencies=[require("units.view")])
async def list_conversions(
    ctx: Access,
    session: SessionDep,
    material_id: Annotated[UUID | None, Query()] = None,
    vendor_id: Annotated[UUID | None, Query()] = None,
    from_unit_id: Annotated[UUID | None, Query()] = None,
    to_unit_id: Annotated[UUID | None, Query()] = None,
    current_only: Annotated[bool, Query()] = True,
) -> list[ConversionRead]:
    rows = await units_service.list_conversions(
        session,
        ctx,
        material_id=material_id,
        vendor_id=vendor_id,
        from_unit_id=from_unit_id,
        to_unit_id=to_unit_id,
        current_only=current_only,
    )
    return [await _conversion_read(session, row) for row in rows]


@conversions.get(
    "/history",
    response_model=list[ConversionRead],
    dependencies=[require("units.view")],
    summary="Full effective-dated history for one unit pair and scope",
)
async def conversion_history(
    ctx: Access,
    session: SessionDep,
    from_unit_id: Annotated[UUID, Query()],
    to_unit_id: Annotated[UUID, Query()],
    scope_type: Annotated[ConversionScope, Query()] = ConversionScope.GLOBAL,
    material_id: Annotated[UUID | None, Query()] = None,
    vendor_id: Annotated[UUID | None, Query()] = None,
) -> list[ConversionRead]:
    rows = await units_service.get_conversion_history(
        session,
        ctx,
        from_unit_id=from_unit_id,
        to_unit_id=to_unit_id,
        scope_type=scope_type,
        material_id=material_id,
        vendor_id=vendor_id,
    )
    return [await _conversion_read(session, row) for row in rows]


@conversions.post(
    "",
    response_model=ConversionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("units.manage_conversions")],
    summary="Enter a conversion factor directly (supersedes any current one)",
)
async def create_conversion(payload: ConversionCreate, ctx: Access, uow: UowDep) -> ConversionRead:
    conversion = await units_service.create_or_supersede_conversion(
        uow.session,
        ctx,
        from_unit_id=payload.from_unit_id,
        to_unit_id=payload.to_unit_id,
        factor=payload.factor,
        scope_type=payload.scope_type,
        material_id=payload.material_id,
        vendor_id=payload.vendor_id,
        effective_from=payload.effective_from,
        basis_note=payload.basis_note,
    )
    return await _conversion_read(uow.session, conversion)


@conversions.post(
    "/resolve",
    response_model=ConversionResolveResponse,
    dependencies=[require("units.view")],
    summary="Resolve the applicable factor for a pair — the simulator (§5.7)",
)
async def resolve_conversion(
    payload: ConversionResolveRequest, ctx: Access, session: SessionDep
) -> ConversionResolveResponse:
    converter = UnitConverter(session, ctx.company_id)
    resolved = await converter.factor(
        payload.from_unit_id,
        payload.to_unit_id,
        material_id=payload.material_id,
        vendor_id=payload.vendor_id,
        at=payload.at,
    )
    converted_quantity = None
    if payload.quantity is not None:
        result = await converter.convert(
            payload.quantity,
            payload.from_unit_id,
            payload.to_unit_id,
            material_id=payload.material_id,
            vendor_id=payload.vendor_id,
            at=payload.at,
        )
        converted_quantity = result.converted_quantity

    return ConversionResolveResponse(
        factor=resolved.factor,
        conversion_id=resolved.conversion_id,
        scope=resolved.scope.value,
        inverted=resolved.inverted,
        via_unit_code=resolved.via_unit_code,
        converted_quantity=converted_quantity,
    )


async def _conversion_read(session: AsyncSession, row: UnitConversion) -> ConversionRead:
    from_code = await session.scalar(select(Unit.code).where(Unit.id == row.from_unit_id))
    to_code = await session.scalar(select(Unit.code).where(Unit.id == row.to_unit_id))
    return ConversionRead.model_validate(row).model_copy(
        update={"from_unit_code": from_code, "to_unit_code": to_code}
    )


# -----------------------------------------------------------------------------
# Calibration
# -----------------------------------------------------------------------------

calibrations = APIRouter(prefix="/unit-conversions/calibrations", tags=["units"])


@calibrations.post(
    "",
    response_model=CalibrationReadingRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("units.manage_conversions")],
    summary="Log one weighbridge reading — does not change any live factor",
)
async def record_calibration_reading(
    payload: CalibrationReadingCreate, ctx: Access, uow: UowDep
) -> CalibrationReadingRead:
    reading = await calibration_service.record_reading(
        uow.session,
        ctx,
        material_id=payload.material_id,
        vendor_id=payload.vendor_id,
        from_unit_id=payload.from_unit_id,
        to_unit_id=payload.to_unit_id,
        source_quantity=payload.source_quantity,
        target_quantity=payload.target_quantity,
        recorded_at=payload.recorded_at,
        truck_number=payload.truck_number,
        weighbridge_ref=payload.weighbridge_ref,
        notes=payload.notes,
    )
    return CalibrationReadingRead.model_validate(reading)


@calibrations.get(
    "",
    response_model=list[CalibrationReadingRead],
    dependencies=[require("units.view")],
)
async def list_calibration_readings(
    ctx: Access,
    session: SessionDep,
    material_id: Annotated[UUID | None, Query()] = None,
    status_filter: Annotated[CalibrationStatus | None, Query(alias="status")] = None,
) -> list[CalibrationReadingRead]:
    rows = await calibration_service.list_readings(
        session, ctx, material_id=material_id, status=status_filter
    )
    return [CalibrationReadingRead.model_validate(r) for r in rows]


@calibrations.get(
    "/stats",
    response_model=CalibrationStatsResponse,
    dependencies=[require("units.view")],
    summary="Pending readings with average/median/spread, for review before confirming",
)
async def calibration_stats(
    ctx: Access,
    session: SessionDep,
    material_id: Annotated[UUID, Query()],
    from_unit_id: Annotated[UUID, Query()],
    to_unit_id: Annotated[UUID, Query()],
    vendor_id: Annotated[UUID | None, Query()] = None,
) -> CalibrationStatsResponse:
    stats = await calibration_service.get_stats(
        session,
        ctx,
        material_id=material_id,
        vendor_id=vendor_id,
        from_unit_id=from_unit_id,
        to_unit_id=to_unit_id,
    )
    return CalibrationStatsResponse(
        count=stats.count,
        average_factor=stats.average_factor,
        median_factor=stats.median_factor,
        min_factor=stats.min_factor,
        max_factor=stats.max_factor,
        spread_pct=stats.spread_pct,
        readings=[CalibrationReadingRead.model_validate(r) for r in stats.readings],
    )


@calibrations.post(
    "/{reading_id}/discard",
    response_model=CalibrationReadingRead,
    dependencies=[require("units.manage_conversions")],
)
async def discard_calibration_reading(
    reading_id: UUID, payload: CalibrationDiscard, ctx: Access, uow: UowDep
) -> CalibrationReadingRead:
    reading = await calibration_service.discard_reading(
        uow.session, ctx, reading_id=reading_id, reason=payload.reason
    )
    return CalibrationReadingRead.model_validate(reading)


@calibrations.post(
    "/confirm",
    response_model=ConversionRead,
    dependencies=[require("units.manage_conversions")],
    summary="Confirm a factor derived from named readings — creates a new effective-dated row",
)
async def confirm_calibration(
    payload: CalibrationConfirm, ctx: Access, uow: UowDep
) -> ConversionRead:
    conversion = await calibration_service.confirm_calibration(
        uow.session,
        ctx,
        material_id=payload.material_id,
        vendor_id=payload.vendor_id,
        from_unit_id=payload.from_unit_id,
        to_unit_id=payload.to_unit_id,
        factor=payload.factor,
        reading_ids=payload.reading_ids,
        effective_from=payload.effective_from,
        basis_note=payload.basis_note,
    )
    return await _conversion_read(uow.session, conversion)


# -----------------------------------------------------------------------------
# Material categories
# -----------------------------------------------------------------------------

categories = APIRouter(prefix="/material-categories", tags=["materials"])


@categories.get(
    "", response_model=Page[MaterialCategoryRead], dependencies=[require("materials.view")]
)
async def list_categories(
    ctx: Access, session: SessionDep, page: PageDep, q: Annotated[str | None, Query()] = None
) -> Page[MaterialCategoryRead]:
    repo = materials_service.category_repository(session)
    rows, total = await repo.list(ctx, "materials.view", page=page, search=q)
    return Page[MaterialCategoryRead].of(
        [MaterialCategoryRead.model_validate(r) for r in rows], params=page, total=total
    )


@categories.post(
    "",
    response_model=MaterialCategoryRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("materials.create")],
)
async def create_category(
    payload: MaterialCategoryCreate, ctx: Access, uow: UowDep
) -> MaterialCategoryRead:
    category = await materials_service.create_category(
        uow.session, ctx, payload=payload.model_dump()
    )
    return MaterialCategoryRead.model_validate(category)


# -----------------------------------------------------------------------------
# Materials
# -----------------------------------------------------------------------------

materials = APIRouter(prefix="/materials", tags=["materials"])


@materials.get("", response_model=Page[MaterialListItem], dependencies=[require("materials.view")])
async def list_materials(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: Annotated[str | None, Query()] = None,
    category_id: Annotated[UUID | None, Query()] = None,
) -> Page[MaterialListItem]:
    repo = materials_service.material_repository(session)
    rows, total = await repo.list(
        ctx, "materials.view", page=page, search=q, filters={"category_id": category_id}
    )

    category_names = dict(
        (
            await session.execute(
                select(MaterialCategory.id, MaterialCategory.name).where(
                    MaterialCategory.id.in_([r.category_id for r in rows])
                )
            )
        )
        .tuples()
        .all()
    )
    unit_codes = dict(
        (
            await session.execute(
                select(Unit.id, Unit.code).where(Unit.id.in_([r.base_unit_id for r in rows]))
            )
        )
        .tuples()
        .all()
    )

    items = [
        MaterialListItem.model_validate(row).model_copy(
            update={
                "category_name": category_names.get(row.category_id),
                "base_unit_code": unit_codes.get(row.base_unit_id),
            }
        )
        for row in rows
    ]
    return Page[MaterialListItem].of(items, params=page, total=total)


@materials.post(
    "",
    response_model=MaterialDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("materials.create")],
)
async def create_material(payload: MaterialCreate, ctx: Access, uow: UowDep) -> MaterialDetail:
    data = payload.model_dump(exclude={"alternate_units"})
    data["tracking_type"] = payload.tracking_type.value
    material = await materials_service.create_material(
        uow.session,
        ctx,
        payload=data,
        alternate_units=[u.model_dump() for u in payload.alternate_units],
    )
    units = await materials_service.list_alternate_units(uow.session, material.id)
    return _material_detail(material, units)


@materials.get(
    "/{material_id}", response_model=MaterialDetail, dependencies=[require("materials.view")]
)
async def get_material(material_id: UUID, ctx: Access, session: SessionDep) -> MaterialDetail:
    repo = materials_service.material_repository(session)
    material = await repo.get(ctx, "materials.view", material_id)
    units = await materials_service.list_alternate_units(session, material.id)
    return _material_detail(material, units)


def _material_detail(material: Material, units: Sequence[MaterialUnit]) -> MaterialDetail:
    """Build a MaterialDetail without letting pydantic touch the ORM object's
    `alternate_units` relationship directly.

    `Material.alternate_units` is `lazy="selectin"`, which is only populated
    as part of an *awaited* SELECT that loaded the object. A material that was
    just constructed and flushed (rather than fetched) never triggers that
    load, and `MaterialDetail.model_validate(material)` — which reads every
    field its class declares, including `alternate_units` — would then touch
    an unloaded async relationship outside of an `await`, raising
    `MissingGreenlet`. Validating into `MaterialRead` first (which declares no
    `alternate_units` field) sidesteps the attribute entirely, regardless of
    whether the relationship happened to already be loaded.
    """
    base = MaterialRead.model_validate(material)
    return MaterialDetail(
        **base.model_dump(),
        alternate_units=[MaterialAlternateUnitRead.model_validate(u) for u in units],
    )


@materials.patch(
    "/{material_id}", response_model=MaterialRead, dependencies=[require("materials.update")]
)
async def update_material(
    material_id: UUID,
    payload: MaterialUpdate,
    ctx: Access,
    uow: UowDep,
    if_match: Annotated[int | None, Header(alias="If-Match")] = None,
) -> MaterialRead:
    material = await materials_service.update_material(
        uow.session,
        ctx,
        material_id=material_id,
        changes=payload.model_dump(exclude_unset=True),
        expected_version=if_match,
    )
    return MaterialRead.model_validate(material)


@materials.post(
    "/{material_id}/units",
    response_model=MaterialAlternateUnitRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("materials.update")],
)
async def add_alternate_unit(
    material_id: UUID, payload: MaterialAlternateUnitIn, ctx: Access, uow: UowDep
) -> MaterialAlternateUnitRead:
    link = await materials_service.add_alternate_unit(
        uow.session,
        ctx,
        material_id=material_id,
        unit_id=payload.unit_id,
        is_purchase_default=payload.is_purchase_default,
        is_issue_default=payload.is_issue_default,
        is_capture_default=payload.is_capture_default,
    )
    return MaterialAlternateUnitRead.model_validate(link)


@materials.post(
    "/{material_id}/deactivate",
    response_model=MaterialRead,
    dependencies=[require("materials.deactivate")],
)
async def deactivate_material(material_id: UUID, ctx: Access, uow: UowDep) -> MaterialRead:
    repo = materials_service.material_repository(uow.session)
    material = await repo.get_for_update(ctx, "materials.view", material_id)
    await repo.soft_delete(material)
    return MaterialRead.model_validate(material)


# -----------------------------------------------------------------------------
# Truck types
# -----------------------------------------------------------------------------

truck_types = APIRouter(prefix="/truck-types", tags=["materials"])


@truck_types.get("", response_model=Page[TruckTypeRead], dependencies=[require("materials.view")])
async def list_truck_types(
    ctx: Access, session: SessionDep, page: PageDep, q: Annotated[str | None, Query()] = None
) -> Page[TruckTypeRead]:
    repo = warehouse_service.truck_type_repository(session)
    rows, total = await repo.list(ctx, "materials.view", page=page, search=q)
    return Page[TruckTypeRead].of(
        [TruckTypeRead.model_validate(r) for r in rows], params=page, total=total
    )


@truck_types.post(
    "",
    response_model=TruckTypeRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("settings.manage_rules")],
    summary="Default truck-type tonnage seeds the TONNAGE_MAX business rule",
)
async def create_truck_type(payload: TruckTypeCreate, ctx: Access, uow: UowDep) -> TruckTypeRead:
    truck_type = await warehouse_service.create_truck_type(
        uow.session, ctx, payload=payload.model_dump()
    )
    return TruckTypeRead.model_validate(truck_type)


# -----------------------------------------------------------------------------
# Warehouses
# -----------------------------------------------------------------------------

warehouses = APIRouter(prefix="/warehouses", tags=["materials"])


@warehouses.get("", response_model=Page[WarehouseRead], dependencies=[require("warehouses.view")])
async def list_warehouses(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    site_id: Annotated[UUID | None, Query()] = None,
) -> Page[WarehouseRead]:
    repo = warehouse_service.warehouse_repository(session)
    rows, total = await repo.list(ctx, "warehouses.view", page=page, filters={"site_id": site_id})
    return Page[WarehouseRead].of(
        [WarehouseRead.model_validate(r) for r in rows], params=page, total=total
    )


@warehouses.post(
    "",
    response_model=WarehouseRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("warehouses.manage")],
)
async def create_warehouse(payload: WarehouseCreate, ctx: Access, uow: UowDep) -> WarehouseRead:
    data = payload.model_dump()
    data["warehouse_type"] = payload.warehouse_type.value
    warehouse = await warehouse_service.create_warehouse(uow.session, ctx, payload=data)
    return WarehouseRead.model_validate(warehouse)


router.include_router(units)
router.include_router(conversions)
router.include_router(calibrations)
router.include_router(categories)
router.include_router(materials)
router.include_router(truck_types)
router.include_router(warehouses)

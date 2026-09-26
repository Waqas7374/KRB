"""What issues, transfers and adjustments have in common: checking lines and
turning a quantity as entered into the ledger's base unit."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.errors import BusinessRuleError, ConversionNotConfiguredError, ValidationError
from app.core.scoping import assert_in_scope
from app.modules.masterdata.services import material_lookup, warehouse_lookup
from app.modules.masterdata.services.conversion import UnitConverter
from app.modules.org.services import company_service
from app.platform.numbering import next_number


def fail(field: str, message: str) -> ValidationError:
    return ValidationError(
        message, errors=[{"field": field, "code": "invalid", "message": message}]
    )


def human(status: str) -> str:
    return status.lower().replace("_", " ")


async def next_doc_number(session: AsyncSession, ctx: AccessContext, doc_type: str) -> str:
    return await next_number(
        session,
        company_id=ctx.company_id,
        doc_type=doc_type,
        fiscal_year_start_month=await company_service.fiscal_year_start_month(
            session, ctx.company_id
        ),
    )


async def warehouse_in_scope(
    session: AsyncSession,
    ctx: AccessContext,
    warehouse_id: UUID,
    permission: str,
    *,
    field: str = "warehouse_id",
) -> warehouse_lookup.WarehouseInfo:
    """A warehouse the caller may act on. One outside their scope reads as
    unknown, the same as anywhere else in the system."""
    warehouse = await warehouse_lookup.get(
        session, company_id=ctx.company_id, warehouse_id=warehouse_id
    )
    if warehouse is None:
        raise fail(field, "Unknown warehouse")
    assert_in_scope(
        ctx,
        permission,
        company_id=ctx.company_id,
        project_id=warehouse.project_id,
        site_id=warehouse.site_id,
        entity="Warehouse",
    )
    return warehouse


async def stockable_materials(
    session: AsyncSession, ctx: AccessContext, material_ids: Iterable[UUID]
) -> dict[UUID, material_lookup.MaterialInfo]:
    """Every material must exist and be one that is held in stock."""
    wanted = set(material_ids)
    found = await material_lookup.materials(session, company_id=ctx.company_id, material_ids=wanted)
    errors: list[dict[str, str]] = []
    for index, material_id in enumerate(material_ids):
        material = found.get(material_id)
        if material is None:
            errors.append(
                {"field": f"lines.{index}.material_id", "code": "invalid", "message": "unknown"}
            )
        elif not material.is_stockable:
            errors.append(
                {
                    "field": f"lines.{index}.material_id",
                    "code": "invalid",
                    "message": f"{material.sku} is not held in stock",
                }
            )
    if errors:
        raise ValidationError("Some lines are not valid.", errors=errors)
    return found


async def to_base(
    converter: UnitConverter,
    *,
    quantity: Decimal,
    unit_id: UUID,
    material: material_lookup.MaterialInfo,
    on: date,
) -> Decimal:
    """The quantity in the material's base unit, the ledger's one unit."""
    if unit_id == material.base_unit_id:
        return quantity
    try:
        converted = await converter.convert(
            quantity, unit_id, material.base_unit_id, material_id=material.id, at=on
        )
    except ConversionNotConfiguredError as exc:
        raise BusinessRuleError(
            "conversion_missing",
            f"{exc}. Set up the conversion factor, or use the material's base unit.",
        ) from exc
    return converted.converted_quantity

"""Checks every procurement document line shares: the material exists, may be
bought, and is quoted or ordered in a unit that material actually has."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.modules.masterdata.services import material_lookup


async def check_materials_and_units(
    session: AsyncSession,
    *,
    company_id: UUID,
    lines: Sequence[tuple[UUID, UUID]],
    path: str = "items",
) -> dict[UUID, material_lookup.MaterialInfo]:
    """Refuse the whole document with one error per bad line, so a form can
    mark every problem at once instead of one per round trip."""
    materials = await material_lookup.materials(
        session, company_id=company_id, material_ids={m for m, _ in lines}
    )
    errors: list[dict[str, str]] = []
    for index, (material_id, unit_id) in enumerate(lines):
        info = materials.get(material_id)
        where = f"{path}.{index}"
        if info is None or not info.is_active:
            errors.append(
                {
                    "field": f"{where}.material_id",
                    "code": "invalid",
                    "message": "unknown or deactivated material",
                }
            )
            continue
        if not info.is_purchasable:
            errors.append(
                {
                    "field": f"{where}.material_id",
                    "code": "invalid",
                    "message": f"{info.sku} is not purchasable",
                }
            )
        if unit_id not in info.unit_ids:
            errors.append(
                {
                    "field": f"{where}.unit_id",
                    "code": "invalid",
                    "message": f"not a unit configured for {info.sku}",
                }
            )
    if errors:
        raise ValidationError("Some lines are not valid.", errors=errors)
    return materials

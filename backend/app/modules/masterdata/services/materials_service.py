"""Material categories, materials and their alternate-unit associations."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import BusinessRuleError
from app.core.types import uuid7
from app.modules.masterdata.models import Material, MaterialCategory, MaterialUnit


def category_repository(session: AsyncSession) -> ScopedRepository[MaterialCategory]:
    return ScopedRepository(
        session,
        MaterialCategory,
        entity_name="Material category",
        sortable={"code", "name", "sequence", "created_at"},
        searchable=("code", "name"),
        default_sort="sequence",
    )


def material_repository(session: AsyncSession) -> ScopedRepository[Material]:
    return ScopedRepository(
        session,
        Material,
        entity_name="Material",
        sortable={"sku", "name", "category_id", "standard_rate", "created_at", "updated_at"},
        searchable=("sku", "name", "description"),
        default_sort="name",
    )


async def create_category(
    session: AsyncSession, ctx: AccessContext, *, payload: dict[str, Any]
) -> MaterialCategory:
    repo = category_repository(session)
    await repo.assert_code_available(ctx.company_id, "code", payload["code"])
    return await repo.create(company_id=ctx.company_id, **payload)


async def create_material(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    payload: dict[str, Any],
    alternate_units: list[dict[str, Any]],
) -> Material:
    repo = material_repository(session)
    await repo.assert_code_available(ctx.company_id, "sku", payload["sku"])

    material = Material(id=uuid7(), company_id=ctx.company_id, **payload)
    session.add(material)
    await session.flush()

    for unit_spec in alternate_units:
        session.add(
            MaterialUnit(
                id=uuid7(), company_id=ctx.company_id, material_id=material.id, **unit_spec
            )
        )

    # A material must always be transactable in its own base unit.
    base_unit_present = any(u["unit_id"] == material.base_unit_id for u in alternate_units)
    if not base_unit_present:
        session.add(
            MaterialUnit(
                id=uuid7(),
                company_id=ctx.company_id,
                material_id=material.id,
                unit_id=material.base_unit_id,
                is_capture_default=not alternate_units,
            )
        )

    await session.flush()
    return material


async def update_material(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    material_id: UUID,
    changes: dict[str, Any],
    expected_version: int | None = None,
) -> Material:
    repo = material_repository(session)
    material = await repo.get_for_update(ctx, "materials.view", material_id)
    repo.apply_update(material, changes, expected_version=expected_version)
    return material


async def add_alternate_unit(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    material_id: UUID,
    unit_id: UUID,
    is_purchase_default: bool,
    is_issue_default: bool,
    is_capture_default: bool,
) -> MaterialUnit:
    await material_repository(session).get(ctx, "materials.view", material_id)

    existing = (
        await session.execute(
            select(MaterialUnit).where(
                MaterialUnit.material_id == material_id, MaterialUnit.unit_id == unit_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise BusinessRuleError(
            "unit_already_linked", "This unit is already linked to the material."
        )

    if is_purchase_default:
        await _clear_default(session, material_id, "is_purchase_default")
    if is_issue_default:
        await _clear_default(session, material_id, "is_issue_default")
    if is_capture_default:
        await _clear_default(session, material_id, "is_capture_default")

    link = MaterialUnit(
        id=uuid7(),
        company_id=ctx.company_id,
        material_id=material_id,
        unit_id=unit_id,
        is_purchase_default=is_purchase_default,
        is_issue_default=is_issue_default,
        is_capture_default=is_capture_default,
    )
    session.add(link)
    await session.flush()
    return link


async def _clear_default(session: AsyncSession, material_id: UUID, field: str) -> None:
    links = (
        (await session.execute(select(MaterialUnit).where(MaterialUnit.material_id == material_id)))
        .scalars()
        .all()
    )
    for link in links:
        if getattr(link, field):
            setattr(link, field, False)


async def list_alternate_units(session: AsyncSession, material_id: UUID) -> list[MaterialUnit]:
    return list(
        (
            await session.execute(
                select(MaterialUnit).where(
                    MaterialUnit.material_id == material_id, MaterialUnit.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )

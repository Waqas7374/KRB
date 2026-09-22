"""Role and permission-catalogue endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import Access, PageDep, SessionDep, UowDep, require
from app.core.pagination import Page
from app.modules.access.schemas import (
    PermissionRead,
    RoleCreate,
    RoleDetail,
    RolePermissionsUpdate,
    RoleRead,
)
from app.modules.access.services import role_service

router = APIRouter()

permissions = APIRouter(prefix="/permissions", tags=["access"])
roles = APIRouter(prefix="/roles", tags=["access"])


@permissions.get(
    "",
    response_model=list[PermissionRead],
    dependencies=[require("roles.view")],
    summary="The full permission catalogue, as code defines it",
)
async def list_permissions() -> list[PermissionRead]:
    return [PermissionRead.model_validate(p) for p in role_service.list_permission_catalogue()]


@roles.get("", response_model=Page[RoleRead], dependencies=[require("roles.view")])
async def list_roles(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: Annotated[str | None, Query()] = None,
) -> Page[RoleRead]:
    repo = role_service.role_repository(session)
    rows, total = await repo.list(ctx, "roles.view", page=page, search=q)
    return Page[RoleRead].of([RoleRead.model_validate(r) for r in rows], params=page, total=total)


@roles.post(
    "",
    response_model=RoleRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("roles.manage")],
)
async def create_role(payload: RoleCreate, ctx: Access, uow: UowDep) -> RoleRead:
    role = await role_service.create_role(uow.session, ctx, payload=payload.model_dump())
    return RoleRead.model_validate(role)


@roles.get("/{role_id}", response_model=RoleDetail, dependencies=[require("roles.view")])
async def get_role(role_id: UUID, ctx: Access, session: SessionDep) -> RoleDetail:
    repo = role_service.role_repository(session)
    role = await repo.get(ctx, "roles.view", role_id)
    codes = await role_service.get_role_permission_codes(session, role.id)
    return RoleDetail.model_validate(role).model_copy(update={"permission_codes": sorted(codes)})


@roles.put(
    "/{role_id}/permissions",
    response_model=RoleDetail,
    dependencies=[require("roles.manage")],
    summary="Replace the role's permission set — refuses to hand out what the caller lacks",
)
async def set_role_permissions(
    role_id: UUID, payload: RolePermissionsUpdate, ctx: Access, uow: UowDep
) -> RoleDetail:
    role = await role_service.set_role_permissions(
        uow.session, ctx, role_id=role_id, permission_codes=set(payload.permission_codes)
    )
    codes = await role_service.get_role_permission_codes(uow.session, role.id)
    return RoleDetail.model_validate(role).model_copy(update={"permission_codes": sorted(codes)})


router.include_router(permissions)
router.include_router(roles)

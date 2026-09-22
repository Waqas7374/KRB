"""User administration endpoints: invite, edit, deactivate, reset, role grants.

Separate from `routes.py` (the `/auth` self-service endpoints) because this is
a different concern: someone managing *other* people's accounts, gated by
`users.*` permissions rather than merely being signed in.

Role-grant endpoints delegate to `access.services.grants` — a
`UserRoleGrant` is an access-module row, so identity reaches it only through
that module's public surface, never its ORM models.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, status

from app.api.deps import Access, PageDep, SessionDep, UowDep, require
from app.core.config import settings
from app.core.logging import get_logger
from app.core.pagination import Page
from app.modules.access.services import grants as grant_service
from app.modules.identity.admin_schemas import (
    PasswordResetIssued,
    RoleGrantCreate,
    RoleGrantRead,
    RoleGrantRevoke,
    UserAdminRead,
    UserAdminUpdate,
    UserDeactivate,
    UserInvite,
    UserInviteResponse,
)
from app.modules.identity.services import user_service

log = get_logger("api.users")

router = APIRouter()


def _grant_read(record: object) -> RoleGrantRead:
    return RoleGrantRead.model_validate(record)


@router.get("", response_model=Page[UserAdminRead], dependencies=[require("users.view")])
async def list_users(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: Annotated[str | None, Query(description="Search name, email or phone")] = None,
    status_filter: Annotated[list[str] | None, Query(alias="status")] = None,
) -> Page[UserAdminRead]:
    repo = user_service.user_repository(session)
    rows, total = await repo.list(
        ctx, "users.view", page=page, search=q, filters={"status": status_filter}
    )
    return Page[UserAdminRead].of(
        [UserAdminRead.model_validate(r) for r in rows], params=page, total=total
    )


@router.post(
    "",
    response_model=UserInviteResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("users.create")],
)
async def invite_user(payload: UserInvite, ctx: Access, uow: UowDep) -> UserInviteResponse:
    user, token = await user_service.invite_user(
        uow.session,
        ctx,
        email=payload.email,
        phone=payload.phone,
        full_name=payload.full_name,
        default_project_id=payload.default_project_id,
        default_site_id=payload.default_site_id,
    )
    # The delivery channel (email) lands with the notification worker; in the
    # meantime the token is logged in development only, exactly as the
    # forgot-password flow does, and never returned in the response.
    if not settings.is_production:
        log.debug("user.invite_token", user_id=str(user.id), token=token)
    return UserInviteResponse(user=UserAdminRead.model_validate(user))


@router.get("/{user_id}", response_model=UserAdminRead, dependencies=[require("users.view")])
async def get_user(user_id: UUID, ctx: Access, session: SessionDep) -> UserAdminRead:
    repo = user_service.user_repository(session)
    user = await repo.get(ctx, "users.view", user_id)
    return UserAdminRead.model_validate(user)


@router.patch("/{user_id}", response_model=UserAdminRead, dependencies=[require("users.update")])
async def update_user(
    user_id: UUID,
    payload: UserAdminUpdate,
    ctx: Access,
    uow: UowDep,
    if_match: Annotated[int | None, Header(alias="If-Match")] = None,
) -> UserAdminRead:
    user = await user_service.update_user(
        uow.session,
        ctx,
        user_id=user_id,
        changes=payload.model_dump(exclude_unset=True),
        expected_version=if_match,
    )
    return UserAdminRead.model_validate(user)


@router.post(
    "/{user_id}/deactivate",
    response_model=UserAdminRead,
    dependencies=[require("users.deactivate")],
)
async def deactivate_user(
    user_id: UUID, payload: UserDeactivate, ctx: Access, uow: UowDep
) -> UserAdminRead:
    user = await user_service.deactivate_user(
        uow.session, ctx, user_id=user_id, reason=payload.reason
    )
    return UserAdminRead.model_validate(user)


@router.post(
    "/{user_id}/reset-password",
    response_model=PasswordResetIssued,
    dependencies=[require("users.reset_password")],
)
async def admin_reset_password(user_id: UUID, ctx: Access, uow: UowDep) -> PasswordResetIssued:
    user, token = await user_service.admin_reset_password(uow.session, ctx, user_id=user_id)
    if not settings.is_production:
        log.debug("user.admin_reset_token", user_id=str(user.id), token=token)
    return PasswordResetIssued(user=UserAdminRead.model_validate(user))


# -----------------------------------------------------------------------------
# Role grants
# -----------------------------------------------------------------------------


@router.get(
    "/{user_id}/role-grants",
    response_model=list[RoleGrantRead],
    dependencies=[require("users.view")],
)
async def list_role_grants(user_id: UUID, ctx: Access, session: SessionDep) -> list[RoleGrantRead]:
    records = await grant_service.list_all_grants(session, user_id)
    return [_grant_read(r) for r in records]


@router.post(
    "/{user_id}/role-grants",
    response_model=RoleGrantRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("users.assign_roles")],
    summary="Grant a role — refuses to hand out permissions the caller does not hold",
)
async def grant_role(
    user_id: UUID, payload: RoleGrantCreate, ctx: Access, uow: UowDep
) -> RoleGrantRead:
    record = await grant_service.grant_role(
        uow.session,
        ctx,
        user_id=user_id,
        role_id=payload.role_id,
        scope_type=payload.scope_type,
        scope_id=payload.scope_id,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        reason=payload.reason,
    )
    return _grant_read(record)


@router.post(
    "/role-grants/{grant_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require("users.assign_roles")],
    # POST rather than DELETE: a body carrying the reason is part of the
    # record, and DELETE-with-body support is inconsistent across clients.
)
async def revoke_role(grant_id: UUID, payload: RoleGrantRevoke, ctx: Access, uow: UowDep) -> None:
    await grant_service.revoke_role(uow.session, ctx, grant_id=grant_id, reason=payload.reason)

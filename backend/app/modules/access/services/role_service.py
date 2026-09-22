"""Role administration: list roles and the permission catalogue, edit a
role's permission set.

The Super Administrator role is locked — `is_locked` — and its permission set
is kept in exact step with the catalogue by the seeder (see
`app.seeds.foundation.seed_roles`), never by hand. Every other role is
editable, so a customer can tighten or loosen a role without a code change.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.core.types import uuid7
from app.modules.access.domain.permissions import ALL_PERMISSIONS
from app.modules.access.models import Permission, Role, RolePermission
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.services.writer import record as record_audit


def role_repository(session: AsyncSession) -> ScopedRepository[Role]:
    return ScopedRepository(
        session,
        Role,
        entity_name="Role",
        sortable={"code", "name", "created_at"},
        searchable=("code", "name", "description"),
        default_sort="name",
    )


def list_permission_catalogue() -> list[Permission]:
    """The catalogue as code defines it — not a database read.

    Used to render the "what could this role hold" picker; the seeded
    `permissions` table is the same data, kept in step by
    `app.seeds.foundation.seed_permissions`.
    """
    return [
        Permission(
            id=uuid7(),
            code=p.code,
            module=p.module,
            action=p.action,
            description=p.description,
            is_restricted=p.restricted,
        )
        for p in ALL_PERMISSIONS
    ]


async def get_role_permission_codes(session: AsyncSession, role_id: UUID) -> set[str]:
    rows = (
        await session.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
        )
    ).scalars()
    return set(rows.all())


async def create_role(
    session: AsyncSession, ctx: AccessContext, *, payload: dict[str, Any]
) -> Role:
    repo = role_repository(session)
    await repo.assert_code_available(ctx.company_id, "code", payload["code"])
    role = Role(
        id=uuid7(),
        company_id=ctx.company_id,
        is_system=False,
        is_locked=False,
        is_assignable=True,
        is_read_only=False,
        **payload,
    )
    session.add(role)
    await session.flush()

    await record_audit(
        session,
        action=AuditAction.CREATE,
        entity_type="Role",
        entity_id=role.id,
        entity_label=role.name,
        company_id=ctx.company_id,
        summary=f"Role created: {role.name}",
    )
    return role


async def set_role_permissions(
    session: AsyncSession, ctx: AccessContext, *, role_id: UUID, permission_codes: set[str]
) -> Role:
    """Replace a role's permission set wholesale.

    Refuses to hand out a permission the caller does not hold, and refuses to
    touch the Super Administrator role, whose permission set is derived from
    the catalogue, not curated by hand.
    """
    role = (
        await session.execute(
            select(Role).where(Role.id == role_id, Role.company_id == ctx.company_id)
        )
    ).scalar_one_or_none()
    if role is None:
        raise NotFoundError("Role", role_id)
    if role.is_locked:
        raise BusinessRuleError(
            "role_is_locked",
            f"The role '{role.name}' is locked and its permissions cannot be edited directly.",
        )

    if not ctx.is_superuser and not permission_codes <= ctx.permissions:
        missing = permission_codes - ctx.permissions
        raise PermissionDeniedError(
            detail=(
                "You cannot grant permissions you do not hold: " + ", ".join(sorted(missing)[:5])
            )
        )

    # `.all()` before dict(): a bare TupleResult exposes keys(), so dict()
    # would treat the result object itself as a mapping and try to subscript
    # it rather than iterating its rows.
    permission_ids = dict(
        (
            await session.execute(
                select(Permission.code, Permission.id).where(Permission.code.in_(permission_codes))
            )
        )
        .tuples()
        .all()
    )
    unknown = permission_codes - set(permission_ids)
    if unknown:
        raise BusinessRuleError(
            "unknown_permission", f"Unknown permission code(s): {', '.join(sorted(unknown))}"
        )

    current = (
        (await session.execute(select(RolePermission).where(RolePermission.role_id == role_id)))
        .scalars()
        .all()
    )
    current_by_permission = {link.permission_id: link for link in current}

    wanted_ids = set(permission_ids.values())
    for permission_id, link in current_by_permission.items():
        if permission_id not in wanted_ids:
            await session.delete(link)
    for permission_id in wanted_ids - set(current_by_permission):
        session.add(RolePermission(role_id=role_id, permission_id=permission_id))

    role.is_read_only = permission_codes <= _read_only_codes()
    role.version += 1

    # The session has autoflush disabled, so without this the route handler's
    # follow-up `get_role_permission_codes` read (a fresh SELECT) would see
    # the state from before this call rather than the pending delete/insert.
    await session.flush()

    await record_audit(
        session,
        action=AuditAction.UPDATE,
        entity_type="Role",
        entity_id=role.id,
        entity_label=role.name,
        company_id=ctx.company_id,
        summary=f"Permissions updated for role '{role.name}' ({len(permission_codes)} held)",
        new_values={"permission_count": len(permission_codes)},
    )
    return role


def _read_only_codes() -> frozenset[str]:
    from app.modules.access.domain.permissions import READ_ONLY_CODES

    return READ_ONLY_CODES | {"audit.view", "approvals.view"}

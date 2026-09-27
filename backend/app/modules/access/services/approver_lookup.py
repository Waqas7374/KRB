"""Access questions the approval engine asks, answered without it having to
know how roles and grants are stored.

* Who holds role R with a grant that covers this document?
* Does role R carry permission P (validating a workflow before it is saved)?
* Can user U, today, exercise permission P on this document?
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.access.domain.enums import ScopeType
from app.modules.access.models import Permission, Role, RolePermission, UserRoleGrant
from app.modules.access.services.resolver import resolve
from app.modules.identity.services import user_lookup


def _coverage(
    *, project_id: UUID | None, site_id: UUID | None, department_id: UUID | None
) -> list[ColumnElement[bool]]:
    """Grants whose scope covers a document at these dimensions."""
    coverage: list[ColumnElement[bool]] = [
        UserRoleGrant.scope_type.in_([ScopeType.GLOBAL.value, ScopeType.COMPANY.value])
    ]
    if project_id is not None:
        coverage.append(
            and_(
                UserRoleGrant.scope_type == ScopeType.PROJECT.value,
                UserRoleGrant.scope_id == project_id,
            )
        )
    if site_id is not None:
        coverage.append(
            and_(
                UserRoleGrant.scope_type == ScopeType.SITE.value, UserRoleGrant.scope_id == site_id
            )
        )
    if department_id is not None:
        coverage.append(
            and_(
                UserRoleGrant.scope_type == ScopeType.DEPARTMENT.value,
                UserRoleGrant.scope_id == department_id,
            )
        )

    return coverage


async def users_holding_role(
    session: AsyncSession,
    *,
    company_id: UUID,
    role_code: str,
    project_id: UUID | None,
    site_id: UUID | None,
    department_id: UUID | None,
    on: date,
) -> set[UUID]:
    """Users whose grant *of this role* covers the document's dimensions.

    Coverage is per grant, deliberately: someone who is Procurement Manager
    for one site and Project Manager for another project must not satisfy a
    "Procurement Manager" step for that other project.
    """
    coverage = _coverage(project_id=project_id, site_id=site_id, department_id=department_id)

    rows = await session.execute(
        select(UserRoleGrant.user_id)
        .join(Role, Role.id == UserRoleGrant.role_id)
        .where(
            Role.company_id == company_id,
            Role.code == role_code,
            UserRoleGrant.revoked_at.is_(None),
            or_(UserRoleGrant.valid_from.is_(None), UserRoleGrant.valid_from <= on),
            or_(UserRoleGrant.valid_to.is_(None), UserRoleGrant.valid_to >= on),
            or_(*coverage),
        )
    )
    return set(rows.scalars().all())


async def users_with_permission(
    session: AsyncSession,
    *,
    company_id: UUID,
    permission: str,
    project_id: UUID | None,
    site_id: UUID | None,
    on: date,
    include_global: bool = False,
) -> set[UUID]:
    """Active users holding `permission` through a grant that covers this place.

    Coverage is per grant, as in `users_holding_role`. A *global* grant (the super
    administrator's) is left out unless asked for: it covers everything, so
    counting it would tell the super administrator about every delivery at every
    site, which is noise rather than routing.
    """
    coverage = _coverage(project_id=project_id, site_id=site_id, department_id=None)
    if not include_global:
        # `_coverage` puts "global or company" first; keep only "company".
        coverage[0] = UserRoleGrant.scope_type == ScopeType.COMPANY.value
    rows = await session.execute(
        select(UserRoleGrant.user_id)
        .join(Role, Role.id == UserRoleGrant.role_id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(
            Role.company_id == company_id,
            Permission.code == permission,
            UserRoleGrant.revoked_at.is_(None),
            or_(UserRoleGrant.valid_from.is_(None), UserRoleGrant.valid_from <= on),
            or_(UserRoleGrant.valid_to.is_(None), UserRoleGrant.valid_to >= on),
            or_(*coverage),
        )
        .distinct()
    )
    candidates = set(rows.scalars().all())
    people = await user_lookup.people(session, company_id=company_id, user_ids=candidates)
    return {u for u in candidates if u in people and people[u].can_act}


async def role_permission_codes(
    session: AsyncSession, *, company_id: UUID, role_code: str
) -> frozenset[str] | None:
    """The permissions a role carries, or None if there is no such role."""
    role_id = await session.scalar(
        select(Role.id).where(Role.company_id == company_id, Role.code == role_code)
    )
    if role_id is None:
        return None
    rows = await session.execute(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
    )
    return frozenset(rows.scalars().all())


async def can_act(
    session: AsyncSession,
    *,
    user_id: UUID,
    company_id: UUID,
    permission: str,
    project_id: UUID | None,
    site_id: UUID | None,
    department_id: UUID | None,
) -> bool:
    """Whether the user holds `permission` with a scope covering the document.

    Resolved fresh from the database rather than from the Redis cache: this
    runs for a handful of candidate approvers at the moment a step activates
    or a decision is made, and it must reflect a grant revoked a minute ago.
    """
    summary = await user_lookup.get_user_summary(session, user_id=user_id, company_id=company_id)
    ctx = await resolve(
        session,
        user_id=user_id,
        company_id=company_id,
        permissions_version=summary.permissions_version,
    )
    if not ctx.has(permission):
        return False
    return ctx.scope_for(permission).covers(
        company_id=company_id,
        project_id=project_id,
        site_id=site_id,
        department_id=department_id,
    )


@dataclass(frozen=True, slots=True)
class HeldRole:
    id: UUID
    code: str
    permissions: frozenset[str]


async def roles_held(
    session: AsyncSession,
    *,
    company_id: UUID,
    user_id: UUID,
    project_id: UUID | None,
    site_id: UUID | None,
    department_id: UUID | None,
    on: date,
) -> list[HeldRole]:
    """The roles a user holds through grants that cover a document today, with
    the permissions each carries. Per grant, as in `users_holding_role`."""
    coverage = _coverage(project_id=project_id, site_id=site_id, department_id=department_id)
    role_rows = (
        await session.execute(
            select(Role.id, Role.code)
            .join(UserRoleGrant, UserRoleGrant.role_id == Role.id)
            .where(
                Role.company_id == company_id,
                UserRoleGrant.user_id == user_id,
                UserRoleGrant.revoked_at.is_(None),
                or_(UserRoleGrant.valid_from.is_(None), UserRoleGrant.valid_from <= on),
                or_(UserRoleGrant.valid_to.is_(None), UserRoleGrant.valid_to >= on),
                or_(*coverage),
            )
            .distinct()
        )
    ).all()
    held: list[HeldRole] = []
    for role_id, code in role_rows:
        codes = (
            await session.execute(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id == role_id)
            )
        ).scalars()
        held.append(HeldRole(id=role_id, code=code, permissions=frozenset(codes)))
    return held

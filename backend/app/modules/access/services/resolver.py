"""Resolve a user's grants into an `AccessContext`.

Called once per request. The result is cached in Redis under
`access:{user_id}:{permissions_version}` — changing a grant bumps
`users.permissions_version`, which invalidates the entry without a cache sweep
and without waiting for a TTL. Revoking a role therefore takes effect on the
very next request, which is the reason permissions are not embedded in the JWT.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.access import AccessContext, ScopeSet
from app.core.logging import get_logger
from app.core.types import today_utc
from app.modules.access.domain.enums import ScopeType
from app.modules.access.domain.roles import SUPER_ADMIN_CODE
from app.modules.access.models import Role, RolePermission, UserRoleGrant

log = get_logger("access")

CACHE_PREFIX = "access"
CACHE_TTL_SECONDS = 15 * 60


def cache_key(user_id: UUID, permissions_version: int) -> str:
    return f"{CACHE_PREFIX}:{user_id}:{permissions_version}"


async def resolve(
    session: AsyncSession,
    *,
    user_id: UUID,
    company_id: UUID,
    permissions_version: int,
) -> AccessContext:
    """Build the context from the database.

    One query with eager loading: a user typically holds one to three grants,
    so this is a small join rather than something worth denormalising.
    """
    today = today_utc()

    stmt = (
        select(UserRoleGrant)
        .where(
            UserRoleGrant.user_id == user_id,
            UserRoleGrant.revoked_at.is_(None),
        )
        .options(selectinload(UserRoleGrant.role).selectinload(Role.permissions))
    )
    grants = list((await session.execute(stmt)).scalars().unique())

    # Validity is filtered in Python rather than SQL because a grant with no
    # dates is open-ended, and expressing that as three-way NULL logic in SQL
    # reads far worse than this.
    active = [
        grant
        for grant in grants
        if (grant.valid_from is None or grant.valid_from <= today)
        and (grant.valid_to is None or grant.valid_to >= today)
    ]

    company_ids: set[UUID] = {company_id}
    permissions: set[str] = set()
    scopes: dict[str, ScopeSet] = {}
    role_codes: set[str] = set()
    is_superuser = False
    all_roles_read_only = bool(active)

    # A site-scoped grant also makes that site's project readable as a record,
    # so a site manager can see which project they are working on. It does NOT
    # widen access to the project's other sites — see ScopeSet.parent_project_ids.
    site_scope_ids = [
        grant.scope_id
        for grant in active
        if grant.scope_type == ScopeType.SITE.value and grant.scope_id is not None
    ]
    parent_projects = await _parent_projects_of(session, site_scope_ids)

    per_permission: defaultdict[str, list[ScopeSet]] = defaultdict(list)

    for grant in active:
        role = grant.role
        role_codes.add(role.code)
        company_ids.add(grant.company_id)
        if role.code == SUPER_ADMIN_CODE:
            is_superuser = True
        if not role.is_read_only:
            all_roles_read_only = False

        scope = _scope_of(grant, parent_projects)
        for link in role.permissions:
            code = _permission_code(link)
            if code is None:
                continue
            permissions.add(code)
            per_permission[code].append(scope)

    for code, scope_list in per_permission.items():
        merged = scope_list[0]
        for extra in scope_list[1:]:
            merged = merged.merge(extra)
        scopes[code] = merged

    return AccessContext(
        user_id=user_id,
        company_id=company_id,
        company_ids=frozenset(company_ids),
        permissions=frozenset(permissions),
        scopes=scopes,
        permissions_version=permissions_version,
        is_superuser=is_superuser,
        is_read_only=all_roles_read_only and not is_superuser,
        role_codes=frozenset(role_codes),
    )


def _permission_code(link: RolePermission) -> str | None:
    """The permission code behind a role link, if it is loaded."""
    permission = link.permission
    return permission.code if permission is not None else None


async def _parent_projects_of(session: AsyncSession, site_ids: list[UUID]) -> dict[UUID, UUID]:
    """site id -> its project id, for the sites a user is scoped to."""
    if not site_ids:
        return {}
    from app.modules.org.services.site_lookup import project_ids_for_sites

    return await project_ids_for_sites(session, site_ids)


def _scope_of(grant: UserRoleGrant, parent_projects: dict[UUID, UUID]) -> ScopeSet:
    scope_type = ScopeType(grant.scope_type)
    if scope_type is ScopeType.GLOBAL:
        return ScopeSet(is_global=True)
    if scope_type is ScopeType.COMPANY:
        return ScopeSet(company_ids=frozenset({grant.company_id}))
    if grant.scope_id is None:
        # The CHECK constraint prevents this; treated as no access rather than
        # accidentally widening to the whole company.
        log.warning("access.grant_missing_scope_id", grant_id=str(grant.id))
        return ScopeSet()
    if scope_type is ScopeType.PROJECT:
        return ScopeSet(project_ids=frozenset({grant.scope_id}))
    if scope_type is ScopeType.SITE:
        parent = parent_projects.get(grant.scope_id)
        return ScopeSet(
            site_ids=frozenset({grant.scope_id}),
            parent_project_ids=frozenset({parent}) if parent else frozenset(),
        )
    return ScopeSet(department_ids=frozenset({grant.scope_id}))


# -----------------------------------------------------------------------------
# Caching
# -----------------------------------------------------------------------------


def _serialise(ctx: AccessContext) -> str:
    return json.dumps(
        {
            "user_id": str(ctx.user_id),
            "company_id": str(ctx.company_id),
            "company_ids": [str(c) for c in ctx.company_ids],
            "permissions": sorted(ctx.permissions),
            "permissions_version": ctx.permissions_version,
            "is_superuser": ctx.is_superuser,
            "is_read_only": ctx.is_read_only,
            "role_codes": sorted(ctx.role_codes),
            "scopes": {
                code: {
                    "g": scope.is_global,
                    "c": [str(i) for i in scope.company_ids],
                    "p": [str(i) for i in scope.project_ids],
                    "s": [str(i) for i in scope.site_ids],
                    "d": [str(i) for i in scope.department_ids],
                    "pp": [str(i) for i in scope.parent_project_ids],
                }
                for code, scope in ctx.scopes.items()
            },
        },
        separators=(",", ":"),
    )


def _deserialise(raw: str) -> AccessContext:
    data: dict[str, Any] = json.loads(raw)
    return AccessContext(
        user_id=UUID(data["user_id"]),
        company_id=UUID(data["company_id"]),
        company_ids=frozenset(UUID(c) for c in data["company_ids"]),
        permissions=frozenset(data["permissions"]),
        scopes={
            code: ScopeSet(
                is_global=scope["g"],
                company_ids=frozenset(UUID(i) for i in scope["c"]),
                project_ids=frozenset(UUID(i) for i in scope["p"]),
                site_ids=frozenset(UUID(i) for i in scope["s"]),
                department_ids=frozenset(UUID(i) for i in scope["d"]),
                parent_project_ids=frozenset(UUID(i) for i in scope.get("pp", [])),
            )
            for code, scope in data["scopes"].items()
        },
        permissions_version=data["permissions_version"],
        is_superuser=data["is_superuser"],
        is_read_only=data["is_read_only"],
        role_codes=frozenset(data["role_codes"]),
    )


async def resolve_cached(
    session: AsyncSession,
    *,
    user_id: UUID,
    company_id: UUID,
    permissions_version: int,
    cache: Any | None = None,
) -> AccessContext:
    """Resolve, preferring a cached copy.

    A cache failure is never fatal: authorisation falls back to the database.
    Getting slower is acceptable; getting permissive is not.
    """
    key = cache_key(user_id, permissions_version)

    if cache is not None:
        try:
            cached = await cache.get(key)
            if cached:
                return _deserialise(cached.decode() if isinstance(cached, bytes) else str(cached))
        except Exception:
            log.warning("access.cache_read_failed", exc_info=True)

    ctx = await resolve(
        session, user_id=user_id, company_id=company_id, permissions_version=permissions_version
    )

    if cache is not None:
        try:
            await cache.set(key, _serialise(ctx), ex=CACHE_TTL_SECONDS)
        except Exception:
            log.warning("access.cache_write_failed", exc_info=True)

    return ctx

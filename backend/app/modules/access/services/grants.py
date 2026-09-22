"""Role-grant queries and mutations exposed to other modules.

`list_active_grants` answers "what roles does this person hold, and where" —
what a profile screen needs. `grant_role`/`revoke_role` are the write path;
they live here rather than in `identity` because a `UserRoleGrant` is
fundamentally an access-module row, and deciding whether a grant is legal
(does it fit the role's allowed scopes, does the granter hold everything the
role grants) is access-module business.

Identity is reached only through its own public surface
(`identity.services.user_lookup`) — never its ORM models — which is what
keeps this the mirror image of `identity` reaching `access` the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.core.types import utcnow, uuid7
from app.modules.access.domain.enums import ScopeType
from app.modules.access.models import Role, UserRoleGrant
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.services.writer import record as record_audit
from app.modules.identity.services import user_lookup
from app.platform import outbox


@dataclass(frozen=True, slots=True)
class GrantSummary:
    role_code: str
    role_name: str
    scope_type: str
    scope_id: UUID | None
    valid_from: date | None
    valid_to: date | None
    is_read_only: bool


@dataclass(frozen=True, slots=True)
class GrantRecord:
    """A grant row plus the role fields an admin screen needs to render it,
    without the caller having to know `Role` exists."""

    id: UUID
    user_id: UUID
    role_id: UUID
    role_code: str
    role_name: str
    scope_type: str
    scope_id: UUID | None
    valid_from: date | None
    valid_to: date | None
    revoked_at: date | None
    grant_reason: str | None
    created_at: datetime


async def list_active_grants(session: AsyncSession, user_id: UUID) -> list[GrantSummary]:
    """Every live grant for a user, newest role first.

    Revoked grants are excluded; their history is in the audit log, which is
    where an access review should look for what someone *used* to hold.
    """
    rows = (
        (
            await session.execute(
                select(UserRoleGrant, Role)
                .join(Role, Role.id == UserRoleGrant.role_id)
                .where(
                    UserRoleGrant.user_id == user_id,
                    UserRoleGrant.revoked_at.is_(None),
                )
                .order_by(Role.code)
            )
        )
        .tuples()
        .all()
    )

    return [
        GrantSummary(
            role_code=role.code,
            role_name=role.name,
            scope_type=grant.scope_type,
            scope_id=grant.scope_id,
            valid_from=grant.valid_from,
            valid_to=grant.valid_to,
            is_read_only=role.is_read_only,
        )
        for grant, role in rows
    ]


async def list_all_grants(session: AsyncSession, user_id: UUID) -> list[GrantRecord]:
    """Every grant, active or revoked — the admin view of one user's access
    history. Newest first."""
    rows = (
        (
            await session.execute(
                select(UserRoleGrant, Role)
                .join(Role, Role.id == UserRoleGrant.role_id)
                .where(UserRoleGrant.user_id == user_id)
                .order_by(UserRoleGrant.created_at.desc())
            )
        )
        .tuples()
        .all()
    )
    return [_to_record(grant, role) for grant, role in rows]


def _to_record(grant: UserRoleGrant, role: Role) -> GrantRecord:
    return GrantRecord(
        id=grant.id,
        user_id=grant.user_id,
        role_id=grant.role_id,
        role_code=role.code,
        role_name=role.name,
        scope_type=grant.scope_type,
        scope_id=grant.scope_id,
        valid_from=grant.valid_from,
        valid_to=grant.valid_to,
        revoked_at=grant.revoked_at,
        grant_reason=grant.grant_reason,
        created_at=grant.created_at,
    )


async def grant_role(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    user_id: UUID,
    role_id: UUID,
    scope_type: ScopeType,
    scope_id: UUID | None,
    valid_from: date | None,
    valid_to: date | None,
    reason: str | None,
) -> GrantRecord:
    """Grant a role at a scope.

    Refuses to grant a permission the granter does not themselves hold — a
    Procurement Manager cannot mint a Finance Manager, even one scoped to a
    single project, by way of this endpoint. Bumps `permissions_version` so
    the grant takes effect on the target's very next request rather than
    waiting for their cached AccessContext to expire.
    """
    target = await user_lookup.get_user_summary(session, user_id=user_id, company_id=ctx.company_id)

    role = (
        await session.execute(
            select(Role).where(Role.id == role_id, Role.company_id == ctx.company_id)
        )
    ).scalar_one_or_none()
    if role is None:
        raise NotFoundError("Role", role_id)
    if not role.is_assignable:
        raise BusinessRuleError(
            "role_not_assignable", f"The role '{role.name}' cannot be assigned directly."
        )
    if scope_type.value not in role.allowed_scope_types:
        raise BusinessRuleError(
            "scope_not_allowed_for_role",
            f"The role '{role.name}' cannot be granted at {scope_type.value} scope.",
        )

    if not ctx.is_superuser:
        role_permission_codes = {link.permission.code for link in role.permissions}
        missing = role_permission_codes - ctx.permissions
        if missing:
            raise PermissionDeniedError(
                detail=(
                    "You cannot grant a role containing permissions you do not hold: "
                    + ", ".join(sorted(missing)[:5])
                )
            )

    existing = (
        await session.execute(
            select(UserRoleGrant).where(
                UserRoleGrant.user_id == user_id,
                UserRoleGrant.role_id == role_id,
                UserRoleGrant.scope_type == scope_type.value,
                UserRoleGrant.scope_id == scope_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.revoked_at is None:
        raise BusinessRuleError(
            "grant_already_exists", "This user already holds this role at this scope."
        )

    grant = UserRoleGrant(
        id=uuid7(),
        company_id=ctx.company_id,
        user_id=user_id,
        role_id=role_id,
        scope_type=scope_type.value,
        scope_id=scope_id,
        valid_from=valid_from,
        valid_to=valid_to,
        granted_by_id=ctx.user_id,
        grant_reason=reason,
    )
    session.add(grant)
    # `created_at` has a server-side default (func.now()), so it is still None
    # on the in-memory object until a flush executes the INSERT.
    await session.flush()

    await user_lookup.bump_permissions_version(session, user_id=user_id)

    await record_audit(
        session,
        action=AuditAction.ROLE_GRANTED,
        entity_type="UserRoleGrant",
        entity_id=grant.id,
        entity_label=f"{role.code} @ {scope_type.value}" + (f":{scope_id}" if scope_id else ""),
        company_id=ctx.company_id,
        summary=f"Granted {role.name} to {target.full_name} at {scope_type.value} scope"
        + (f": {reason}" if reason else ""),
    )
    await outbox.emit(
        session,
        outbox.DomainEvent(
            event_type="user.role_granted",
            aggregate_type="User",
            aggregate_id=user_id,
            payload={"role_code": role.code, "scope_type": scope_type.value},
            company_id=ctx.company_id,
        ),
    )
    return _to_record(grant, role)


async def revoke_role(
    session: AsyncSession, ctx: AccessContext, *, grant_id: UUID, reason: str | None
) -> None:
    grant = (
        await session.execute(
            select(UserRoleGrant).where(
                UserRoleGrant.id == grant_id, UserRoleGrant.company_id == ctx.company_id
            )
        )
    ).scalar_one_or_none()
    if grant is None:
        raise NotFoundError("Role grant", grant_id)
    if grant.revoked_at is not None:
        raise BusinessRuleError("grant_already_revoked", "This grant was already revoked.")

    role = (await session.execute(select(Role).where(Role.id == grant.role_id))).scalar_one()
    target = await user_lookup.get_user_summary(
        session, user_id=grant.user_id, company_id=ctx.company_id
    )

    grant.revoked_at = utcnow().date()
    await user_lookup.bump_permissions_version(session, user_id=grant.user_id)

    await record_audit(
        session,
        action=AuditAction.ROLE_REVOKED,
        entity_type="UserRoleGrant",
        entity_id=grant.id,
        entity_label=f"{role.code} @ {grant.scope_type}",
        company_id=ctx.company_id,
        summary=f"Revoked {role.name} from {target.full_name}" + (f": {reason}" if reason else ""),
    )
    await outbox.emit(
        session,
        outbox.DomainEvent(
            event_type="user.role_revoked",
            aggregate_type="User",
            aggregate_id=grant.user_id,
            payload={"role_code": role.code},
            company_id=ctx.company_id,
        ),
    )

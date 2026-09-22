"""Project, site, department and cost-centre use-cases."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import BusinessRuleError, NotFoundError, StateTransitionError
from app.core.logging import get_logger
from app.core.types import utcnow, uuid7
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.services.writer import record as record_audit
from app.modules.org.domain.enums import (
    DEFAULT_PROJECT_PHASES,
    PhaseStatus,
    ProjectStatus,
    SiteType,
)
from app.modules.org.models import CostCenter, Department, Project, ProjectPhase, Site
from app.modules.org.services import geo
from app.platform import outbox

log = get_logger("org")

ALLOWED_PROJECT_TRANSITIONS: dict[ProjectStatus, frozenset[ProjectStatus]] = {
    ProjectStatus.DRAFT: frozenset({ProjectStatus.ACTIVE, ProjectStatus.CANCELLED}),
    ProjectStatus.ACTIVE: frozenset(
        {ProjectStatus.ON_HOLD, ProjectStatus.COMPLETED, ProjectStatus.CANCELLED}
    ),
    ProjectStatus.ON_HOLD: frozenset({ProjectStatus.ACTIVE, ProjectStatus.CANCELLED}),
    ProjectStatus.COMPLETED: frozenset({ProjectStatus.CLOSED, ProjectStatus.ACTIVE}),
    # Terminal. Reopening a closed project would reopen its accounting periods
    # too, which is a decision that needs more than a status change.
    ProjectStatus.CLOSED: frozenset(),
    ProjectStatus.CANCELLED: frozenset(),
}


# -----------------------------------------------------------------------------
# Repositories
# -----------------------------------------------------------------------------


def project_repository(session: AsyncSession) -> ScopedRepository[Project]:
    return ScopedRepository(
        session,
        Project,
        entity_name="Project",
        sortable={
            "code",
            "name",
            "status",
            "project_type",
            "start_date",
            "end_date",
            "created_at",
            "updated_at",
        },
        searchable=("code", "name", "location_name"),
        default_sort="code",
    )


def site_repository(session: AsyncSession) -> ScopedRepository[Site]:
    return ScopedRepository(
        session,
        Site,
        entity_name="Site",
        sortable={"code", "name", "site_type", "city", "created_at", "updated_at"},
        searchable=("code", "name", "city", "address"),
        default_sort="code",
    )


def department_repository(session: AsyncSession) -> ScopedRepository[Department]:
    return ScopedRepository(
        session,
        Department,
        entity_name="Department",
        sortable={"code", "name", "created_at", "updated_at"},
        searchable=("code", "name"),
        default_sort="code",
    )


def cost_center_repository(session: AsyncSession) -> ScopedRepository[CostCenter]:
    return ScopedRepository(
        session,
        CostCenter,
        entity_name="Cost centre",
        sortable={"code", "name", "created_at", "updated_at"},
        searchable=("code", "name"),
        default_sort="code",
    )


# -----------------------------------------------------------------------------
# Projects
# -----------------------------------------------------------------------------


async def create_project(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    payload: dict[str, Any],
    create_default_phases: bool = True,
) -> Project:
    repo = project_repository(session)
    await repo.assert_code_available(ctx.company_id, "code", payload["code"])

    centroid = payload.pop("centroid", None)
    require_po = payload.pop("require_po_for_delivery", False)

    project = Project(
        id=uuid7(),
        company_id=ctx.company_id,
        status=ProjectStatus.DRAFT.value,
        centroid=geo.to_db_point(centroid["latitude"], centroid["longitude"]) if centroid else None,
        settings={"require_po_for_delivery": require_po},
        **payload,
    )
    session.add(project)
    await session.flush()

    if create_default_phases:
        for index, (code, name) in enumerate(DEFAULT_PROJECT_PHASES):
            session.add(
                ProjectPhase(
                    id=uuid7(),
                    company_id=ctx.company_id,
                    project_id=project.id,
                    code=code,
                    name=name,
                    sequence=index * 10,
                    status=PhaseStatus.NOT_STARTED.value,
                )
            )

    await outbox.emit(
        session,
        outbox.DomainEvent(
            event_type="project.created",
            aggregate_type="Project",
            aggregate_id=project.id,
            payload={"code": project.code, "name": project.name},
            company_id=ctx.company_id,
        ),
    )
    log.info("project.created", project_id=str(project.id), code=project.code)
    return project


async def update_project(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    project_id: UUID,
    changes: dict[str, Any],
    expected_version: int | None = None,
) -> Project:
    repo = project_repository(session)
    project = await repo.get_for_update(ctx, "projects.view", project_id)

    if ProjectStatus(project.status).is_terminal:
        raise BusinessRuleError(
            "project_closed",
            f"This project is {project.status.lower()} and can no longer be edited.",
        )

    centroid = changes.pop("centroid", None)
    if centroid is not None:
        project.centroid = geo.to_db_point(centroid["latitude"], centroid["longitude"])

    require_po = changes.pop("require_po_for_delivery", None)
    if require_po is not None:
        project.settings = {**project.settings, "require_po_for_delivery": require_po}

    repo.apply_update(project, changes, expected_version=expected_version)
    return project


async def change_project_status(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    project_id: UUID,
    target: ProjectStatus,
    reason: str | None,
) -> Project:
    repo = project_repository(session)
    project = await repo.get_for_update(ctx, "projects.view", project_id)

    current = ProjectStatus(project.status)
    if target not in ALLOWED_PROJECT_TRANSITIONS[current]:
        raise StateTransitionError("Project", current.value, target.value)

    if target is ProjectStatus.CLOSED:
        open_sites = await session.scalar(
            select(func.count())
            .select_from(Site)
            .where(Site.project_id == project.id, Site.deleted_at.is_(None))
        )
        if open_sites:
            raise BusinessRuleError(
                "project_has_open_sites",
                f"This project still has {open_sites} site(s). "
                "Remove or reassign them before closing the project.",
            )

    project.status = target.value
    project.version += 1
    if target in {ProjectStatus.CLOSED, ProjectStatus.CANCELLED}:
        project.closed_at = utcnow()
        project.close_reason = reason
    if target is ProjectStatus.COMPLETED:
        project.actual_completion_date = utcnow().date()

    await record_audit(
        session,
        action=AuditAction.CLOSE
        if target in {ProjectStatus.CLOSED, ProjectStatus.CANCELLED}
        else AuditAction.UPDATE,
        entity_type="Project",
        entity_id=project.id,
        entity_label=f"{project.code} — {project.name}",
        company_id=ctx.company_id,
        project_id=project.id,
        summary=f"Project {current.value} -> {target.value}" + (f": {reason}" if reason else ""),
        old_values={"status": current.value},
        new_values={"status": target.value},
    )
    await outbox.emit(
        session,
        outbox.DomainEvent(
            event_type=f"project.{target.value.lower()}",
            aggregate_type="Project",
            aggregate_id=project.id,
            payload={"code": project.code, "reason": reason},
            company_id=ctx.company_id,
        ),
    )
    return project


async def site_counts(session: AsyncSession, project_ids: list[UUID]) -> dict[UUID, int]:
    if not project_ids:
        return {}
    rows = (
        await session.execute(
            select(Site.project_id, func.count())
            .where(Site.project_id.in_(project_ids), Site.deleted_at.is_(None))
            .group_by(Site.project_id)
        )
    ).tuples()
    return {project_id: count for project_id, count in rows.all() if project_id is not None}


async def get_phases(session: AsyncSession, project_id: UUID) -> list[ProjectPhase]:
    return list(
        (
            await session.execute(
                select(ProjectPhase)
                .where(ProjectPhase.project_id == project_id, ProjectPhase.deleted_at.is_(None))
                .order_by(ProjectPhase.sequence)
            )
        )
        .scalars()
        .all()
    )


async def update_phase(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    project_id: UUID,
    phase_id: UUID,
    changes: dict[str, Any],
) -> ProjectPhase:
    await project_repository(session).get(ctx, "projects.view", project_id)

    phase = (
        await session.execute(
            select(ProjectPhase).where(
                ProjectPhase.id == phase_id,
                ProjectPhase.project_id == project_id,
                ProjectPhase.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if phase is None:
        raise NotFoundError("Project phase", phase_id)

    for field, value in changes.items():
        if hasattr(phase, field) and getattr(phase, field) != value:
            setattr(phase, field, value)
    phase.version += 1
    return phase


# -----------------------------------------------------------------------------
# Sites
# -----------------------------------------------------------------------------


async def create_site(
    session: AsyncSession, ctx: AccessContext, *, payload: dict[str, Any]
) -> Site:
    repo = site_repository(session)
    await repo.assert_code_available(ctx.company_id, "code", payload["code"])

    project_id = payload.get("project_id")
    if project_id is not None:
        # Confirms the project exists and is within the caller's scope before
        # a site is hung off it.
        await project_repository(session).get(ctx, "projects.view", project_id)

    centroid = payload.pop("centroid", None)
    site = Site(
        id=uuid7(),
        company_id=ctx.company_id,
        centroid=geo.to_db_point(centroid["latitude"], centroid["longitude"]) if centroid else None,
        settings={},
        **payload,
    )
    session.add(site)
    await session.flush()

    await outbox.emit(
        session,
        outbox.DomainEvent(
            event_type="site.created",
            aggregate_type="Site",
            aggregate_id=site.id,
            payload={"code": site.code, "name": site.name},
            company_id=ctx.company_id,
        ),
    )
    return site


async def update_site(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    site_id: UUID,
    changes: dict[str, Any],
    expected_version: int | None = None,
) -> Site:
    repo = site_repository(session)
    site = await repo.get_for_update(ctx, "sites.view", site_id)

    new_type = changes.get("site_type", site.site_type)
    new_project = changes.get("project_id", site.project_id)
    if new_type == SiteType.DEVELOPMENT.value and new_project is None:
        raise BusinessRuleError(
            "development_site_requires_project",
            "A development site must belong to a project.",
        )

    repo.apply_update(site, changes, expected_version=expected_version)
    return site


async def set_geofence(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    site_id: UUID,
    centroid: dict[str, float] | None,
    radius_m: Any | None,
    boundary: list[dict[str, float]] | None,
    clear_boundary: bool,
) -> Site:
    """Set a site's geofence.

    Audited as a distinct event: widening a geofence is how a delivery recorded
    two kilometres away stops being flagged, so the change needs to be as
    visible as the flags it suppresses.
    """
    repo = site_repository(session)
    site = await repo.get_for_update(ctx, "sites.view", site_id)

    before = {
        "geofence_radius_m": str(site.geofence_radius_m),
        "has_polygon": site.boundary is not None,
    }

    if centroid is not None:
        site.centroid = geo.to_db_point(centroid["latitude"], centroid["longitude"])
    if radius_m is not None:
        site.geofence_radius_m = radius_m
    if clear_boundary:
        site.boundary = None
    elif boundary is not None:
        site.boundary = geo.to_db_polygon(
            [(point["latitude"], point["longitude"]) for point in boundary]
        )

    if site.centroid is None and site.boundary is None:
        raise BusinessRuleError(
            "geofence_needs_a_location",
            "A geofence needs either a centre point or a boundary. Without one, "
            "every delivery at this site would be flagged as out of bounds.",
        )

    site.version += 1

    after = {
        "geofence_radius_m": str(site.geofence_radius_m),
        "has_polygon": site.boundary is not None,
    }
    await record_audit(
        session,
        action=AuditAction.RULE_CHANGE,
        entity_type="Site",
        entity_id=site.id,
        entity_label=f"{site.code} — {site.name}",
        company_id=ctx.company_id,
        site_id=site.id,
        project_id=site.project_id,
        summary=(
            f"Geofence changed for {site.code}: radius "
            f"{before['geofence_radius_m']}m -> {after['geofence_radius_m']}m"
            + (", boundary set" if after["has_polygon"] and not before["has_polygon"] else "")
            + (", boundary cleared" if before["has_polygon"] and not after["has_polygon"] else "")
        ),
        old_values=before,
        new_values=after,
    )
    return site

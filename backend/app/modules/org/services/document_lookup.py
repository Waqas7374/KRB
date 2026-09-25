"""Organisation facts a document needs, exposed without the ORM models.

Used by modules that raise documents against a project and site (purchase
requests now; deliveries and GRNs later) to validate the pair, build approval
context, and find the people the approval engine calls "dynamic" approvers.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.models import CostCenter, Department, Project, ProjectPhase, Site


@dataclass(frozen=True, slots=True)
class DocumentPlace:
    """Where a document sits in the organisation, with the names people read."""

    project_id: UUID | None
    project_code: str | None
    project_name: str | None
    project_status: str | None
    project_manager_id: UUID | None
    site_id: UUID | None
    site_code: str | None
    site_name: str | None
    site_manager_id: UUID | None
    department_id: UUID | None
    department_code: str | None
    phase_id: UUID | None
    phase_code: str | None
    cost_center_id: UUID | None
    cost_center_code: str | None


async def codes(
    session: AsyncSession,
    *,
    company_id: UUID,
    project_ids: set[UUID],
    site_ids: set[UUID] | None = None,
    phase_ids: set[UUID] | None = None,
) -> dict[UUID, tuple[str, str]]:
    """id -> (code, name) for a batch of projects, sites and phases, for list
    screens that show codes without a query per row."""
    out: dict[UUID, tuple[str, str]] = {}
    if project_ids:
        rows = await session.execute(
            select(Project.id, Project.code, Project.name).where(
                Project.company_id == company_id, Project.id.in_(list(project_ids))
            )
        )
        out.update({i: (c, n) for i, c, n in rows.tuples().all()})
    if site_ids:
        rows = await session.execute(
            select(Site.id, Site.code, Site.name).where(
                Site.company_id == company_id, Site.id.in_(list(site_ids))
            )
        )
        out.update({i: (c, n) for i, c, n in rows.tuples().all()})
    if phase_ids:
        rows = await session.execute(
            select(ProjectPhase.id, ProjectPhase.code, ProjectPhase.name).where(
                ProjectPhase.id.in_(list(phase_ids))
            )
        )
        out.update({i: (c, n) for i, c, n in rows.tuples().all()})
    return out


class PlaceMismatchError(ValueError):
    """The ids do not form a consistent place (a site from another project...)."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


async def resolve_place(
    session: AsyncSession,
    *,
    company_id: UUID,
    project_id: UUID | None,
    site_id: UUID | None = None,
    department_id: UUID | None = None,
    phase_id: UUID | None = None,
    cost_center_id: UUID | None = None,
) -> DocumentPlace:
    """Load and cross-check a document's organisational dimensions.

    Raises `PlaceMismatchError` naming the offending field when an id does not
    exist in this company, a site belongs to a different project, or a phase
    belongs to a different project.
    """
    project = site = department = phase = cost_center = None

    if project_id is not None:
        project = await session.scalar(
            select(Project).where(Project.id == project_id, Project.company_id == company_id)
        )
        if project is None:
            raise PlaceMismatchError("project_id", "Unknown project")
    if site_id is not None:
        site = await session.scalar(
            select(Site).where(Site.id == site_id, Site.company_id == company_id)
        )
        if site is None:
            raise PlaceMismatchError("site_id", "Unknown site")
        if project_id is not None and site.project_id not in (None, project_id):
            raise PlaceMismatchError("site_id", "This site belongs to a different project")
    if department_id is not None:
        department = await session.scalar(
            select(Department).where(
                Department.id == department_id, Department.company_id == company_id
            )
        )
        if department is None:
            raise PlaceMismatchError("department_id", "Unknown department")
    if phase_id is not None:
        phase = await session.scalar(select(ProjectPhase).where(ProjectPhase.id == phase_id))
        if phase is None or phase.project_id != project_id:
            raise PlaceMismatchError("phase_id", "This phase does not belong to the project")
    if cost_center_id is not None:
        cost_center = await session.scalar(
            select(CostCenter).where(
                CostCenter.id == cost_center_id, CostCenter.company_id == company_id
            )
        )
        if cost_center is None:
            raise PlaceMismatchError("cost_center_id", "Unknown cost centre")

    return DocumentPlace(
        project_id=project.id if project else None,
        project_code=project.code if project else None,
        project_name=project.name if project else None,
        project_status=project.status if project else None,
        project_manager_id=project.manager_user_id if project else None,
        site_id=site.id if site else None,
        site_code=site.code if site else None,
        site_name=site.name if site else None,
        site_manager_id=site.manager_user_id if site else None,
        department_id=department.id if department else None,
        department_code=department.code if department else None,
        phase_id=phase.id if phase else None,
        phase_code=phase.code if phase else None,
        cost_center_id=cost_center.id if cost_center else None,
        cost_center_code=cost_center.code if cost_center else None,
    )

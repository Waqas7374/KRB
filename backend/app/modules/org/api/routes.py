"""Organisation endpoints: projects, phases, sites, departments, cost centres."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, status
from sqlalchemy import select

from app.api.deps import Access, PageDep, SessionDep, UowDep, require
from app.core.pagination import Page
from app.modules.org.domain.enums import ProjectStatus, ProjectType, SiteType
from app.modules.org.models import Project, Site
from app.modules.org.schemas import (
    CostCenterCreate,
    CostCenterRead,
    CostCenterUpdate,
    DepartmentCreate,
    DepartmentRead,
    DepartmentUpdate,
    GeofenceUpdate,
    Point,
    ProjectCreate,
    ProjectDetail,
    ProjectListItem,
    ProjectPhaseRead,
    ProjectPhaseUpdate,
    ProjectRead,
    ProjectStatusChange,
    ProjectUpdate,
    SiteCreate,
    SiteListItem,
    SiteRead,
    SiteUpdate,
)
from app.modules.org.services import geo, project_service

router = APIRouter()


def _point(value: Any) -> Point | None:
    coords = geo.from_db_point(value)
    return Point(latitude=coords.latitude, longitude=coords.longitude) if coords else None


def _project_read(project: Project) -> ProjectRead:
    return ProjectRead.model_validate(project).model_copy(
        update={"centroid": _point(project.centroid)}
    )


def _site_read(site: Site, project_code: str | None = None) -> SiteRead:
    return SiteRead.model_validate(site).model_copy(
        update={"centroid": _point(site.centroid), "project_code": project_code}
    )


# -----------------------------------------------------------------------------
# Projects
# -----------------------------------------------------------------------------

projects = APIRouter(prefix="/projects", tags=["projects"])


@projects.get("", response_model=Page[ProjectListItem], dependencies=[require("projects.view")])
async def list_projects(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: Annotated[str | None, Query(description="Search code, name or location")] = None,
    status_filter: Annotated[list[ProjectStatus] | None, Query(alias="status")] = None,
    project_type: Annotated[list[ProjectType] | None, Query()] = None,
) -> Page[ProjectListItem]:
    repo = project_service.project_repository(session)
    rows, total = await repo.list(
        ctx,
        "projects.view",
        page=page,
        search=q,
        filters={
            "status": [s.value for s in status_filter] if status_filter else None,
            "project_type": [t.value for t in project_type] if project_type else None,
        },
    )
    counts = await project_service.site_counts(session, [row.id for row in rows])
    items = [
        ProjectListItem.model_validate(row).model_copy(update={"site_count": counts.get(row.id, 0)})
        for row in rows
    ]
    return Page[ProjectListItem].of(items, params=page, total=total)


@projects.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("projects.create")],
    summary="Create a project with its standard phases",
)
async def create_project(payload: ProjectCreate, ctx: Access, uow: UowDep) -> ProjectRead:
    data = payload.model_dump(exclude={"create_default_phases"})
    data["project_type"] = payload.project_type.value
    if payload.centroid:
        data["centroid"] = payload.centroid.model_dump()

    project = await project_service.create_project(
        uow.session,
        ctx,
        payload=data,
        create_default_phases=payload.create_default_phases,
    )
    return _project_read(project)


@projects.get(
    "/{project_id}", response_model=ProjectDetail, dependencies=[require("projects.view")]
)
async def get_project(project_id: UUID, ctx: Access, session: SessionDep) -> ProjectDetail:
    repo = project_service.project_repository(session)
    project = await repo.get(ctx, "projects.view", project_id)
    phases = await project_service.get_phases(session, project.id)
    counts = await project_service.site_counts(session, [project.id])

    return ProjectDetail.model_validate(project).model_copy(
        update={
            "centroid": _point(project.centroid),
            "phases": [ProjectPhaseRead.model_validate(phase) for phase in phases],
            "site_count": counts.get(project.id, 0),
        }
    )


@projects.patch(
    "/{project_id}", response_model=ProjectRead, dependencies=[require("projects.update")]
)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    ctx: Access,
    uow: UowDep,
    if_match: Annotated[int | None, Header(alias="If-Match")] = None,
) -> ProjectRead:
    changes = payload.model_dump(exclude_unset=True)
    if "project_type" in changes and payload.project_type is not None:
        changes["project_type"] = payload.project_type.value
    if "centroid" in changes and payload.centroid is not None:
        changes["centroid"] = payload.centroid.model_dump()

    project = await project_service.update_project(
        uow.session, ctx, project_id=project_id, changes=changes, expected_version=if_match
    )
    return _project_read(project)


@projects.post(
    "/{project_id}/status",
    response_model=ProjectRead,
    dependencies=[require("projects.close")],
    summary="Move a project through its lifecycle",
)
async def change_project_status(
    project_id: UUID, payload: ProjectStatusChange, ctx: Access, uow: UowDep
) -> ProjectRead:
    project = await project_service.change_project_status(
        uow.session, ctx, project_id=project_id, target=payload.status, reason=payload.reason
    )
    return _project_read(project)


@projects.get(
    "/{project_id}/phases",
    response_model=list[ProjectPhaseRead],
    dependencies=[require("projects.view")],
)
async def list_phases(project_id: UUID, ctx: Access, session: SessionDep) -> list[ProjectPhaseRead]:
    await project_service.project_repository(session).get(ctx, "projects.view", project_id)
    phases = await project_service.get_phases(session, project_id)
    return [ProjectPhaseRead.model_validate(phase) for phase in phases]


@projects.patch(
    "/{project_id}/phases/{phase_id}",
    response_model=ProjectPhaseRead,
    dependencies=[require("projects.update")],
)
async def update_phase(
    project_id: UUID,
    phase_id: UUID,
    payload: ProjectPhaseUpdate,
    ctx: Access,
    uow: UowDep,
) -> ProjectPhaseRead:
    changes = payload.model_dump(exclude_unset=True)
    if "status" in changes and payload.status is not None:
        changes["status"] = payload.status.value

    phase = await project_service.update_phase(
        uow.session, ctx, project_id=project_id, phase_id=phase_id, changes=changes
    )
    return ProjectPhaseRead.model_validate(phase)


# -----------------------------------------------------------------------------
# Sites
# -----------------------------------------------------------------------------

sites = APIRouter(prefix="/sites", tags=["sites"])


@sites.get("", response_model=Page[SiteListItem], dependencies=[require("sites.view")])
async def list_sites(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: Annotated[str | None, Query()] = None,
    project_id: Annotated[UUID | None, Query()] = None,
    site_type: Annotated[list[SiteType] | None, Query()] = None,
) -> Page[SiteListItem]:
    repo = project_service.site_repository(session)
    rows, total = await repo.list(
        ctx,
        "sites.view",
        page=page,
        search=q,
        filters={
            "project_id": project_id,
            "site_type": [t.value for t in site_type] if site_type else None,
        },
    )

    project_codes = dict(
        (
            await session.execute(
                select(Project.id, Project.code).where(
                    Project.id.in_([row.project_id for row in rows if row.project_id])
                )
            )
        )
        .tuples()
        .all()
    )

    items = [
        SiteListItem.model_validate(row).model_copy(
            update={
                "project_code": project_codes.get(row.project_id) if row.project_id else None,
                "has_polygon_geofence": row.boundary is not None,
            }
        )
        for row in rows
    ]
    return Page[SiteListItem].of(items, params=page, total=total)


@sites.post(
    "",
    response_model=SiteRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("sites.create")],
)
async def create_site(payload: SiteCreate, ctx: Access, uow: UowDep) -> SiteRead:
    data = payload.model_dump()
    data["site_type"] = payload.site_type.value
    if payload.centroid:
        data["centroid"] = payload.centroid.model_dump()

    site = await project_service.create_site(uow.session, ctx, payload=data)
    return _site_read(site)


@sites.get("/{site_id}", response_model=SiteRead, dependencies=[require("sites.view")])
async def get_site(site_id: UUID, ctx: Access, session: SessionDep) -> SiteRead:
    site = await project_service.site_repository(session).get(ctx, "sites.view", site_id)
    project_code = None
    if site.project_id:
        project_code = await session.scalar(
            select(Project.code).where(Project.id == site.project_id)
        )
    return _site_read(site, project_code)


@sites.patch("/{site_id}", response_model=SiteRead, dependencies=[require("sites.update")])
async def update_site(
    site_id: UUID,
    payload: SiteUpdate,
    ctx: Access,
    uow: UowDep,
    if_match: Annotated[int | None, Header(alias="If-Match")] = None,
) -> SiteRead:
    changes = payload.model_dump(exclude_unset=True)
    if "site_type" in changes and payload.site_type is not None:
        changes["site_type"] = payload.site_type.value

    site = await project_service.update_site(
        uow.session, ctx, site_id=site_id, changes=changes, expected_version=if_match
    )
    return _site_read(site)


@sites.put(
    "/{site_id}/geofence",
    response_model=SiteRead,
    dependencies=[require("sites.manage_geofence")],
    summary="Set a site's geofence — audited, because it changes what gets flagged",
)
async def set_geofence(
    site_id: UUID, payload: GeofenceUpdate, ctx: Access, uow: UowDep
) -> SiteRead:
    site = await project_service.set_geofence(
        uow.session,
        ctx,
        site_id=site_id,
        centroid=payload.centroid.model_dump() if payload.centroid else None,
        radius_m=payload.geofence_radius_m,
        boundary=[point.model_dump() for point in payload.boundary] if payload.boundary else None,
        clear_boundary=payload.clear_boundary,
    )
    return _site_read(site)


@sites.get(
    "/{site_id}/geofence",
    dependencies=[require("sites.view")],
    summary="The site's geofence as points",
)
async def get_geofence(site_id: UUID, ctx: Access, session: SessionDep) -> dict[str, Any]:
    site = await project_service.site_repository(session).get(ctx, "sites.view", site_id)
    boundary = geo.from_db_polygon(site.boundary)
    centre = geo.from_db_point(site.centroid)
    return {
        "site_id": str(site.id),
        "centroid": {"latitude": centre.latitude, "longitude": centre.longitude}
        if centre
        else None,
        "geofence_radius_m": str(site.geofence_radius_m),
        "boundary": [{"latitude": p.latitude, "longitude": p.longitude} for p in boundary]
        if boundary
        else None,
        # Which one actually applies: a polygon always wins over a radius.
        "effective_mode": "POLYGON" if boundary else ("RADIUS" if centre else "NONE"),
    }


# -----------------------------------------------------------------------------
# Departments & cost centres
# -----------------------------------------------------------------------------

structure = APIRouter(tags=["organisation"])


@structure.get(
    "/departments", response_model=Page[DepartmentRead], dependencies=[require("departments.view")]
)
async def list_departments(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: Annotated[str | None, Query()] = None,
) -> Page[DepartmentRead]:
    repo = project_service.department_repository(session)
    rows, total = await repo.list(ctx, "departments.view", page=page, search=q)
    return Page[DepartmentRead].of(
        [DepartmentRead.model_validate(row) for row in rows], params=page, total=total
    )


@structure.post(
    "/departments",
    response_model=DepartmentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("departments.manage")],
)
async def create_department(payload: DepartmentCreate, ctx: Access, uow: UowDep) -> DepartmentRead:
    repo = project_service.department_repository(uow.session)
    await repo.assert_code_available(ctx.company_id, "code", payload.code)
    department = await repo.create(company_id=ctx.company_id, **payload.model_dump())
    return DepartmentRead.model_validate(department)


@structure.patch(
    "/departments/{department_id}",
    response_model=DepartmentRead,
    dependencies=[require("departments.manage")],
)
async def update_department(
    department_id: UUID, payload: DepartmentUpdate, ctx: Access, uow: UowDep
) -> DepartmentRead:
    repo = project_service.department_repository(uow.session)
    department = await repo.get_for_update(ctx, "departments.view", department_id)
    repo.apply_update(department, payload.model_dump(exclude_unset=True))
    return DepartmentRead.model_validate(department)


@structure.get(
    "/cost-centers",
    response_model=Page[CostCenterRead],
    dependencies=[require("departments.view")],
)
async def list_cost_centers(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: Annotated[str | None, Query()] = None,
    project_id: Annotated[UUID | None, Query()] = None,
) -> Page[CostCenterRead]:
    repo = project_service.cost_center_repository(session)
    rows, total = await repo.list(
        ctx, "departments.view", page=page, search=q, filters={"project_id": project_id}
    )
    return Page[CostCenterRead].of(
        [CostCenterRead.model_validate(row) for row in rows], params=page, total=total
    )


@structure.post(
    "/cost-centers",
    response_model=CostCenterRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("departments.manage")],
)
async def create_cost_center(payload: CostCenterCreate, ctx: Access, uow: UowDep) -> CostCenterRead:
    repo = project_service.cost_center_repository(uow.session)
    await repo.assert_code_available(ctx.company_id, "code", payload.code)
    cost_center = await repo.create(company_id=ctx.company_id, **payload.model_dump())
    return CostCenterRead.model_validate(cost_center)


@structure.patch(
    "/cost-centers/{cost_center_id}",
    response_model=CostCenterRead,
    dependencies=[require("departments.manage")],
)
async def update_cost_center(
    cost_center_id: UUID, payload: CostCenterUpdate, ctx: Access, uow: UowDep
) -> CostCenterRead:
    repo = project_service.cost_center_repository(uow.session)
    cost_center = await repo.get_for_update(ctx, "departments.view", cost_center_id)
    repo.apply_update(cost_center, payload.model_dump(exclude_unset=True))
    return CostCenterRead.model_validate(cost_center)


router.include_router(projects)
router.include_router(sites)
router.include_router(structure)

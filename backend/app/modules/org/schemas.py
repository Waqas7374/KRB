"""Organisation request and response schemas."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.org.domain.enums import PhaseStatus, ProjectStatus, ProjectType, SiteType
from app.modules.org.services.geo import coerce_point_input


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


Code = Annotated[str, Field(min_length=2, max_length=30, pattern=r"^[A-Z0-9][A-Z0-9\-_/]*$")]

# Pakistan spans roughly 23.5 to 37.1 N and 60.8 to 77.1 E. The bounds are generous
# rather than exact: the point is to catch a swapped lat/lng or a stray digit,
# not to police the border.
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


class Point(ApiModel):
    latitude: Latitude
    longitude: Longitude


# -----------------------------------------------------------------------------
# Projects
# -----------------------------------------------------------------------------


class ProjectPhaseRead(ApiModel):
    id: UUID
    code: str
    name: str
    sequence: int
    status: str
    planned_start: date | None
    planned_end: date | None
    actual_start: date | None
    actual_end: date | None
    progress_pct: Decimal


class ProjectPhaseUpdate(ApiModel):
    name: Annotated[str | None, Field(min_length=2, max_length=160)] = None
    sequence: int | None = None
    status: PhaseStatus | None = None
    planned_start: date | None = None
    planned_end: date | None = None
    actual_start: date | None = None
    actual_end: date | None = None
    progress_pct: Annotated[Decimal | None, Field(ge=0, le=100)] = None


class ProjectCreate(ApiModel):
    code: Code
    name: Annotated[str, Field(min_length=2, max_length=200)]
    description: Annotated[str | None, Field(max_length=2000)] = None
    project_type: ProjectType = ProjectType.HOUSING_SCHEME
    location_name: Annotated[str | None, Field(max_length=200)] = None
    centroid: Point | None = None
    total_area_value: Annotated[Decimal | None, Field(gt=0, max_digits=18, decimal_places=4)] = None
    total_area_unit: Annotated[str | None, Field(max_length=20)] = None
    start_date: date | None = None
    end_date: date | None = None
    manager_user_id: UUID | None = None
    total_budget: Annotated[Decimal | None, Field(ge=0, max_digits=18, decimal_places=4)] = None
    # Decision Q3: deliveries are PO-optional by default, enforceable per project.
    require_po_for_delivery: bool = False
    # Seeds the eight standard phases (Roads, Sewerage, Water, ...).
    create_default_phases: bool = True

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str) -> str:
        return " ".join(value.split())

    @model_validator(mode="after")
    def _dates_ordered(self) -> ProjectCreate:
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("The end date cannot be before the start date")
        return self


class ProjectUpdate(ApiModel):
    """Status is absent: closing a project goes through its own endpoint so it
    carries a reason and an audit action of its own."""

    name: Annotated[str | None, Field(min_length=2, max_length=200)] = None
    description: Annotated[str | None, Field(max_length=2000)] = None
    project_type: ProjectType | None = None
    location_name: Annotated[str | None, Field(max_length=200)] = None
    centroid: Point | None = None
    total_area_value: Annotated[Decimal | None, Field(gt=0, max_digits=18, decimal_places=4)] = None
    total_area_unit: Annotated[str | None, Field(max_length=20)] = None
    start_date: date | None = None
    end_date: date | None = None
    manager_user_id: UUID | None = None
    total_budget: Annotated[Decimal | None, Field(ge=0, max_digits=18, decimal_places=4)] = None
    require_po_for_delivery: bool | None = None


class ProjectRead(ApiModel):
    id: UUID
    code: str
    name: str
    description: str | None
    project_type: str
    status: str
    location_name: str | None
    centroid: Point | None = None
    total_area_value: Decimal | None
    total_area_unit: str | None
    start_date: date | None
    end_date: date | None
    actual_completion_date: date | None
    manager_user_id: UUID | None
    total_budget: Decimal | None
    currency_code: str
    require_po_for_delivery: bool
    closed_at: datetime | None
    close_reason: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    @field_validator("centroid", mode="before")
    @classmethod
    def _read_point(cls, value: object) -> object:
        return coerce_point_input(value)


class ProjectDetail(ProjectRead):
    phases: list[ProjectPhaseRead] = Field(default_factory=list)
    site_count: int = 0


class ProjectListItem(ApiModel):
    id: UUID
    code: str
    name: str
    project_type: str
    status: str
    location_name: str | None
    start_date: date | None
    end_date: date | None
    total_budget: Decimal | None
    currency_code: str
    site_count: int = 0
    updated_at: datetime


class ProjectStatusChange(ApiModel):
    status: ProjectStatus
    reason: Annotated[str | None, Field(max_length=500)] = None

    @model_validator(mode="after")
    def _terminal_needs_a_reason(self) -> ProjectStatusChange:
        if self.status in {ProjectStatus.CLOSED, ProjectStatus.CANCELLED} and not self.reason:
            raise ValueError(f"Moving a project to {self.status.value} requires a reason")
        return self


# -----------------------------------------------------------------------------
# Sites
# -----------------------------------------------------------------------------


class SiteCreate(ApiModel):
    code: Code
    name: Annotated[str, Field(min_length=2, max_length=160)]
    # Nullable on purpose: a central store or head-office yard belongs to the
    # company, not to one project.
    project_id: UUID | None = None
    site_type: SiteType = SiteType.DEVELOPMENT
    address: Annotated[str | None, Field(max_length=500)] = None
    city: Annotated[str | None, Field(max_length=120)] = None
    centroid: Point | None = None
    geofence_radius_m: Annotated[
        Decimal, Field(gt=0, le=50000, max_digits=10, decimal_places=2)
    ] = Decimal("500")
    timezone: Annotated[str, Field(max_length=64)] = "Asia/Karachi"
    manager_user_id: UUID | None = None
    contact_phone: Annotated[str | None, Field(max_length=32)] = None

    @model_validator(mode="after")
    def _development_sites_belong_to_a_project(self) -> SiteCreate:
        if self.site_type is SiteType.DEVELOPMENT and self.project_id is None:
            raise ValueError(
                "A development site must belong to a project. Use CENTRAL_STORE, "
                "HEAD_OFFICE, PLANT or DEPOT for a company-level location."
            )
        return self


class SiteUpdate(ApiModel):
    name: Annotated[str | None, Field(min_length=2, max_length=160)] = None
    project_id: UUID | None = None
    site_type: SiteType | None = None
    address: Annotated[str | None, Field(max_length=500)] = None
    city: Annotated[str | None, Field(max_length=120)] = None
    timezone: Annotated[str | None, Field(max_length=64)] = None
    manager_user_id: UUID | None = None
    contact_phone: Annotated[str | None, Field(max_length=32)] = None


class GeofenceUpdate(ApiModel):
    """Set a site's geofence.

    Either a radius around a centre, or an explicit boundary. A polygon wins
    when present, because a rectangular site is badly described by a circle.
    """

    centroid: Point | None = None
    geofence_radius_m: Annotated[
        Decimal | None, Field(gt=0, le=50000, max_digits=10, decimal_places=2)
    ] = None
    # Closed ring, longitude/latitude order as GeoJSON has it.
    boundary: list[Point] | None = None
    clear_boundary: bool = False

    @model_validator(mode="after")
    def _something_to_set(self) -> GeofenceUpdate:
        if self.boundary is not None and len(self.boundary) < 3:
            raise ValueError("A boundary needs at least three points")
        if (
            self.centroid is None
            and self.geofence_radius_m is None
            and self.boundary is None
            and not self.clear_boundary
        ):
            raise ValueError("Provide a centre, a radius or a boundary")
        return self


class SiteRead(ApiModel):
    id: UUID
    code: str
    name: str
    project_id: UUID | None
    project_code: str | None = None
    site_type: str
    address: str | None
    city: str | None
    centroid: Point | None = None
    geofence_radius_m: Decimal
    has_polygon_geofence: bool
    timezone: str
    manager_user_id: UUID | None
    contact_phone: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    @field_validator("centroid", mode="before")
    @classmethod
    def _read_point(cls, value: object) -> object:
        return coerce_point_input(value)


class SiteListItem(ApiModel):
    id: UUID
    code: str
    name: str
    project_id: UUID | None
    project_code: str | None = None
    site_type: str
    city: str | None
    geofence_radius_m: Decimal
    has_polygon_geofence: bool
    updated_at: datetime


# -----------------------------------------------------------------------------
# Departments & cost centres
# -----------------------------------------------------------------------------


class DepartmentCreate(ApiModel):
    code: Code
    name: Annotated[str, Field(min_length=2, max_length=160)]
    parent_department_id: UUID | None = None
    description: Annotated[str | None, Field(max_length=500)] = None


class DepartmentUpdate(ApiModel):
    name: Annotated[str | None, Field(min_length=2, max_length=160)] = None
    parent_department_id: UUID | None = None
    description: Annotated[str | None, Field(max_length=500)] = None


class DepartmentRead(ApiModel):
    id: UUID
    code: str
    name: str
    parent_department_id: UUID | None
    description: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class CostCenterCreate(ApiModel):
    code: Code
    name: Annotated[str, Field(min_length=2, max_length=160)]
    project_id: UUID | None = None
    department_id: UUID | None = None
    owner_user_id: UUID | None = None
    description: Annotated[str | None, Field(max_length=500)] = None


class CostCenterUpdate(ApiModel):
    name: Annotated[str | None, Field(min_length=2, max_length=160)] = None
    project_id: UUID | None = None
    department_id: UUID | None = None
    owner_user_id: UUID | None = None
    description: Annotated[str | None, Field(max_length=500)] = None


class CostCenterRead(ApiModel):
    id: UUID
    code: str
    name: str
    project_id: UUID | None
    department_id: UUID | None
    owner_user_id: UUID | None
    description: str | None
    version: int
    created_at: datetime
    updated_at: datetime


def _rebuild() -> None:
    ProjectDetail.model_rebuild()


_rebuild()

"""Organisation ORM models.

Company -> Projects -> Phases, plus Sites (company-level, optionally attached to
a project), Departments and Cost Centres.

Sites are deliberately *not* forced under a project: a central store, head-office
yard or shared batching plant belongs to the company, while a development site
belongs to one project. Transactions always carry both `project_id` and
`site_id`, so project costing is unaffected either way.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constraints import enum_check
from app.core.db import BaseModel, MasterDataModel
from app.core.sync import SyncSeqMixin
from app.modules.org.domain.enums import PhaseStatus, ProjectStatus, ProjectType, SiteType

# -----------------------------------------------------------------------------
# Company
# -----------------------------------------------------------------------------


class Company(BaseModel):
    """The tenant root.

    Has no `company_id` of its own — it *is* the company. v1 operates a single
    row, but every other table references this one, so enabling multi-company is
    configuration rather than a migration of every table.
    """

    __tablename__ = "companies"
    __audited__ = True

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(200))

    # Pakistan tax identifiers — docs/12-confirmed-decisions.md Q1.
    ntn: Mapped[str | None] = mapped_column(String(20), comment="National Tax Number")
    strn: Mapped[str | None] = mapped_column(String(20), comment="Sales Tax Registration Number")

    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="PKR")
    fiscal_year_start_month: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Karachi")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en-PK")

    address: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(160))
    logo_storage_key: Mapped[str | None] = mapped_column(String(512))

    # Plumbing flags only. Business rules live in `business_rules`.
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("code", name="uq_companies_code"),
        CheckConstraint(
            "fiscal_year_start_month BETWEEN 1 AND 12", name="fiscal_year_start_month_valid"
        ),
        CheckConstraint("char_length(base_currency) = 3", name="base_currency_length"),
    )


# -----------------------------------------------------------------------------
# Project
# -----------------------------------------------------------------------------


class Project(MasterDataModel):
    __tablename__ = "projects"
    # This table *is* the project dimension: it has no project_id column, so
    # scope filtering matches against its own id. See core/scoping.py.
    __scope_self__ = "PROJECT"

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000))

    project_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ProjectType.HOUSING_SCHEME.value
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ProjectStatus.DRAFT.value
    )

    location_name: Mapped[str | None] = mapped_column(String(200))
    centroid: Mapped[Any | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    total_area_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    total_area_unit: Mapped[str | None] = mapped_column(String(20))

    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    actual_completion_date: Mapped[date | None] = mapped_column(Date)

    manager_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )

    # Indicative only; the authoritative figure is the approved budget.
    total_budget: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="PKR")

    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(500))

    # Per-project overrides, e.g. require_po_for_delivery (decision Q3).
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_projects_company_id_code"),
        enum_check("status", ProjectStatus),
        enum_check("project_type", ProjectType),
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="dates_ordered",
        ),
        CheckConstraint("total_budget IS NULL OR total_budget >= 0", name="budget_non_negative"),
        Index("ix_projects_company_id_status", "company_id", "status"),
        Index(
            "ix_projects_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    phases: Mapped[list[ProjectPhase]] = relationship(
        back_populates="project", lazy="selectin", order_by="ProjectPhase.sequence"
    )

    @property
    def require_po_for_delivery(self) -> bool:
        """Decision Q3: PO-optional by default, enforceable per project."""
        return bool(self.settings.get("require_po_for_delivery", False))


class ProjectPhase(MasterDataModel):
    """Work breakdown: Roads, Sewerage, Water, Electricity, and so on.

    Budgets, cost centres and material issues all charge to a phase, which is
    what makes "how much has Roads cost so far" answerable.
    """

    __tablename__ = "project_phases"

    project_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PhaseStatus.NOT_STARTED.value
    )

    planned_start: Mapped[date | None] = mapped_column(Date)
    planned_end: Mapped[date | None] = mapped_column(Date)
    actual_start: Mapped[date | None] = mapped_column(Date)
    actual_end: Mapped[date | None] = mapped_column(Date)
    progress_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )

    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_project_phases_project_id_code"),
        enum_check("status", PhaseStatus),
        CheckConstraint("progress_pct BETWEEN 0 AND 100", name="progress_pct_range"),
        CheckConstraint(
            "planned_end IS NULL OR planned_start IS NULL OR planned_end >= planned_start",
            name="planned_dates_ordered",
        ),
    )

    project: Mapped[Project] = relationship(back_populates="phases", lazy="noload")


# -----------------------------------------------------------------------------
# Site
# -----------------------------------------------------------------------------


class Site(MasterDataModel, SyncSeqMixin):
    """A physical location where material is delivered, stored or consumed.

    Geofencing (§15) uses `boundary` when present, otherwise a radius around
    `centroid`. Both are geography(4326) so PostGIS returns real metres.
    """

    __tablename__ = "sites"
    __scope_self__ = "SITE"

    # Nullable: a central store or head-office yard has no single project.
    project_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    site_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SiteType.DEVELOPMENT.value
    )

    address: Mapped[str | None] = mapped_column(String(500))
    city: Mapped[str | None] = mapped_column(String(120))

    centroid: Mapped[Any | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    boundary: Mapped[Any | None] = mapped_column(
        Geography(geometry_type="POLYGON", srid=4326, spatial_index=False)
    )
    geofence_radius_m: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("500")
    )

    # Date boundaries ("today's deliveries") follow the site, not the server.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Karachi")

    manager_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    contact_phone: Mapped[str | None] = mapped_column(String(32))

    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_sites_company_id_code"),
        enum_check("site_type", SiteType),
        CheckConstraint("geofence_radius_m > 0", name="geofence_radius_positive"),
        # A development site must belong to a project; other types need not.
        CheckConstraint(
            "site_type <> 'DEVELOPMENT' OR project_id IS NOT NULL",
            name="development_site_requires_project",
        ),
        Index("ix_sites_company_id_project_id", "company_id", "project_id"),
        Index("ix_sites_centroid_gist", "centroid", postgresql_using="gist"),
        Index("ix_sites_boundary_gist", "boundary", postgresql_using="gist"),
    )

    project: Mapped[Project | None] = relationship(lazy="noload")

    @property
    def has_polygon_geofence(self) -> bool:
        return self.boundary is not None


# -----------------------------------------------------------------------------
# Department & cost centre
# -----------------------------------------------------------------------------


class Department(MasterDataModel):
    __tablename__ = "departments"
    __scope_self__ = "DEPARTMENT"

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    parent_department_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("departments.id", ondelete="RESTRICT"), index=True
    )
    # FK to employees is added in Phase 5, when that table exists.
    head_employee_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    description: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_departments_company_id_code"),
        CheckConstraint("parent_department_id <> id", name="no_self_parent"),
    )


class CostCenter(MasterDataModel):
    """A charge bucket, which may or may not be tied to a project.

    Head-office overheads need a cost centre with no project; a project's
    infrastructure spend needs one bound to it.
    """

    __tablename__ = "cost_centers"

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    department_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("departments.id", ondelete="RESTRICT"), index=True
    )
    owner_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    description: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_cost_centers_company_id_code"),
        Index("ix_cost_centers_company_id_project_id", "company_id", "project_id"),
    )


__all__ = [
    "Company",
    "CostCenter",
    "Department",
    "Project",
    "ProjectPhase",
    "Site",
]

"""Phase 1 seed data: company, access control, organisation, users.

Realistic rather than minimal (§43), because a demo dataset that cannot
exercise the permission model is not a test of anything. Three projects, sites
that include a company-level central store, and one user per role so the
authorisation matrix can be run against real grants.

Passwords are the same well-known development string for every seeded user and
`must_change_password` is set, so the accounts are unusable in production
without a reset.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.core.types import utcnow
from app.modules.access.domain.enums import ScopeType
from app.modules.access.domain.permissions import ALL_PERMISSIONS
from app.modules.access.domain.roles import STANDARD_ROLES, validate_catalogue
from app.modules.access.models import Permission, Role, RolePermission, UserRoleGrant
from app.modules.identity.domain.enums import UserStatus
from app.modules.identity.models import User
from app.modules.identity.services.auth import normalise_phone
from app.modules.org.domain.enums import (
    DEFAULT_PROJECT_PHASES,
    PhaseStatus,
    ProjectStatus,
    ProjectType,
    SiteType,
)
from app.modules.org.models import Company, CostCenter, Department, Project, ProjectPhase, Site
from app.seeds.registry import SeedResult, upsert

DEV_PASSWORD = "KrbDev!Passw0rd"  # noqa: S105 — development seed only


def _point(lon: float, lat: float) -> Any:
    """A geography point. Note the order: longitude first, then latitude."""
    return from_shape(Point(lon, lat), srid=4326)


# -----------------------------------------------------------------------------
# Company
# -----------------------------------------------------------------------------


async def seed_company(session: AsyncSession) -> tuple[Company, SeedResult]:
    result = SeedResult("company")
    company = await upsert(
        session,
        Company,
        match={"code": settings.default_company_code},
        values={
            "name": "KRB Developments",
            "legal_name": "KRB Developments (Private) Limited",
            "ntn": "1234567-8",
            "strn": "0412345678901",
            "base_currency": settings.default_currency,
            "fiscal_year_start_month": settings.fiscal_year_start_month,
            "timezone": settings.default_timezone,
            "locale": settings.default_locale,
            "phone": "+92 42 111 000 111",
            "email": "info@krb.example",
            "address": {
                "line1": "12-A Gulberg III",
                "city": "Lahore",
                "province": "Punjab",
                "country": "PK",
            },
            "is_active": True,
        },
        result=result,
        protected_fields=("name", "legal_name", "ntn", "strn", "phone", "email", "address"),
    )
    return company, result


# -----------------------------------------------------------------------------
# Access control
# -----------------------------------------------------------------------------


async def seed_permissions(session: AsyncSession) -> SeedResult:
    """Sync the permission table with the code catalogue.

    Permissions absent from the catalogue are removed, so an endpoint can never
    be authorised by a stale row that no code references.
    """
    result = SeedResult("permissions")

    for definition in ALL_PERMISSIONS:
        await upsert(
            session,
            Permission,
            match={"code": definition.code},
            values={
                "module": definition.module,
                "action": definition.action,
                "description": definition.description,
                "is_restricted": definition.restricted,
            },
            result=result,
        )

    known = {definition.code for definition in ALL_PERMISSIONS}
    existing = (await session.execute(select(Permission))).scalars().all()
    for row in existing:
        if row.code not in known:
            await session.delete(row)
            result.updated += 1

    return result


async def seed_roles(session: AsyncSession, company: Company) -> SeedResult:
    result = SeedResult("roles")
    validate_catalogue()

    permission_ids = {
        code: pid
        for pid, code in (await session.execute(select(Permission.id, Permission.code))).tuples()
    }

    for definition in STANDARD_ROLES:
        role = await upsert(
            session,
            Role,
            match={"company_id": company.id, "code": definition.code},
            values={
                "name": definition.name,
                "description": definition.description,
                "is_system": True,
                "is_locked": definition.is_locked,
                "is_assignable": True,
                "is_read_only": definition.is_read_only,
                "allowed_scope_types": [s.value for s in definition.allowed_scopes],
            },
            result=result,
            # An administrator may legitimately have renamed a role or narrowed
            # its scopes; re-seeding must not undo that.
            protected_fields=("name", "description", "allowed_scope_types"),
        )

        wanted = {permission_ids[code] for code in definition.resolve() if code in permission_ids}
        current = {
            link.permission_id
            for link in (
                await session.execute(
                    select(RolePermission).where(RolePermission.role_id == role.id)
                )
            )
            .scalars()
            .all()
        }

        for permission_id in wanted - current:
            session.add(RolePermission(role_id=role.id, permission_id=permission_id))
        # Locked roles (Super Administrator) are kept exactly in step with the
        # catalogue. Editable roles keep any extra permissions an administrator
        # granted deliberately.
        if definition.is_locked:
            for link in (
                (
                    await session.execute(
                        select(RolePermission).where(
                            RolePermission.role_id == role.id,
                            RolePermission.permission_id.in_(current - wanted),
                        )
                    )
                )
                .scalars()
                .all()
            ):
                await session.delete(link)

    await session.flush()
    return result


# -----------------------------------------------------------------------------
# Organisation
# -----------------------------------------------------------------------------

PROJECTS: tuple[dict[str, Any], ...] = (
    {
        "code": "GVH",
        "name": "Green Valley Housing Project",
        "project_type": ProjectType.HOUSING_SCHEME.value,
        "status": ProjectStatus.ACTIVE.value,
        "location_name": "Bedian Road, Lahore",
        "centroid": (74.2456, 31.4102),
        "total_area_value": Decimal("1200.0000"),
        "total_area_unit": "KANAL",
        "start_date": date(2025, 7, 1),
        "end_date": date(2028, 6, 30),
        "total_budget": Decimal("2800000000.0000"),
    },
    {
        "code": "RSD",
        "name": "Riverside Development",
        "project_type": ProjectType.RESIDENTIAL_COMMUNITY.value,
        "status": ProjectStatus.ACTIVE.value,
        "location_name": "Ravi Riverfront, Lahore",
        "centroid": (74.2011, 31.6023),
        "total_area_value": Decimal("640.0000"),
        "total_area_unit": "KANAL",
        "start_date": date(2026, 1, 15),
        "end_date": date(2029, 12, 31),
        "total_budget": Decimal("1650000000.0000"),
    },
    {
        "code": "CCD",
        "name": "Central Commercial District",
        "project_type": ProjectType.COMMERCIAL.value,
        "status": ProjectStatus.DRAFT.value,
        "location_name": "Main Boulevard, Lahore",
        "centroid": (74.3436, 31.5102),
        "total_area_value": Decimal("180.0000"),
        "total_area_unit": "KANAL",
        "start_date": date(2026, 10, 1),
        "total_budget": Decimal("980000000.0000"),
    },
)

# Sites. `project` None means a company-level location — the case that made
# sites.project_id nullable (see docs/12, Phase 1 question).
SITES: tuple[dict[str, Any], ...] = (
    {
        "code": "GVH-S1",
        "name": "Green Valley — Block A Earthworks",
        "project": "GVH",
        "site_type": SiteType.DEVELOPMENT.value,
        "centroid": (74.2461, 31.4110),
        "geofence_radius_m": Decimal("500.00"),
        "city": "Lahore",
    },
    {
        "code": "GVH-S2",
        "name": "Green Valley — Block C Roads",
        "project": "GVH",
        "site_type": SiteType.DEVELOPMENT.value,
        "centroid": (74.2502, 31.4071),
        "geofence_radius_m": Decimal("350.00"),
        "city": "Lahore",
    },
    {
        "code": "RSD-S1",
        "name": "Riverside — Sector 1 Development",
        "project": "RSD",
        "site_type": SiteType.DEVELOPMENT.value,
        "centroid": (74.2018, 31.6031),
        "geofence_radius_m": Decimal("600.00"),
        "city": "Lahore",
    },
    {
        "code": "CS-LHR",
        "name": "Central Store — Lahore",
        "project": None,
        "site_type": SiteType.CENTRAL_STORE.value,
        "centroid": (74.3110, 31.4890),
        "geofence_radius_m": Decimal("200.00"),
        "city": "Lahore",
    },
    {
        "code": "HO",
        "name": "Head Office — Gulberg",
        "project": None,
        "site_type": SiteType.HEAD_OFFICE.value,
        "centroid": (74.3482, 31.5169),
        "geofence_radius_m": Decimal("150.00"),
        "city": "Lahore",
    },
)

DEPARTMENTS: tuple[tuple[str, str], ...] = (
    ("EXEC", "Executive"),
    ("FIN", "Finance & Accounts"),
    ("HR", "Human Resources"),
    ("PROC", "Procurement"),
    ("PROJ", "Projects & Engineering"),
    ("STORE", "Stores & Inventory"),
    ("ADMIN", "Administration"),
)

COST_CENTERS: tuple[tuple[str, str, str | None], ...] = (
    ("HO-ADMIN", "Head Office Administration", None),
    ("HO-FIN", "Finance & Treasury", None),
    ("GVH-INFRA", "Green Valley Infrastructure", "GVH"),
    ("GVH-BLDG", "Green Valley Buildings", "GVH"),
    ("RSD-INFRA", "Riverside Infrastructure", "RSD"),
    ("CCD-INFRA", "Commercial District Infrastructure", "CCD"),
)


async def seed_org(session: AsyncSession, company: Company) -> SeedResult:
    result = SeedResult("organisation")

    for spec in PROJECTS:
        lon, lat = spec["centroid"]
        project = await upsert(
            session,
            Project,
            match={"company_id": company.id, "code": spec["code"]},
            values={
                "name": spec["name"],
                "project_type": spec["project_type"],
                "status": spec["status"],
                "location_name": spec["location_name"],
                "centroid": _point(lon, lat),
                "total_area_value": spec["total_area_value"],
                "total_area_unit": spec["total_area_unit"],
                "start_date": spec["start_date"],
                "end_date": spec.get("end_date"),
                "total_budget": spec["total_budget"],
                "currency_code": company.base_currency,
                "settings": {},
            },
            result=result,
            protected_fields=("status", "settings", "end_date", "total_budget"),
        )

        for index, (code, name) in enumerate(DEFAULT_PROJECT_PHASES):
            await upsert(
                session,
                ProjectPhase,
                match={"project_id": project.id, "code": code},
                values={
                    "company_id": company.id,
                    "name": name,
                    "sequence": index * 10,
                    "status": PhaseStatus.NOT_STARTED.value,
                },
                result=result,
                protected_fields=("status", "progress_pct"),
            )

    project_ids = {
        code: pid
        for pid, code in (
            await session.execute(
                select(Project.id, Project.code).where(Project.company_id == company.id)
            )
        ).tuples()
    }

    for spec in SITES:
        lon, lat = spec["centroid"]
        await upsert(
            session,
            Site,
            match={"company_id": company.id, "code": spec["code"]},
            values={
                "project_id": project_ids.get(spec["project"]) if spec["project"] else None,
                "name": spec["name"],
                "site_type": spec["site_type"],
                "city": spec["city"],
                "centroid": _point(lon, lat),
                "geofence_radius_m": spec["geofence_radius_m"],
                "timezone": company.timezone,
                "settings": {},
            },
            result=result,
            protected_fields=("geofence_radius_m", "settings"),
        )

    for code, name in DEPARTMENTS:
        await upsert(
            session,
            Department,
            match={"company_id": company.id, "code": code},
            values={"name": name},
            result=result,
            protected_fields=("name",),
        )

    for code, name, project_code in COST_CENTERS:
        await upsert(
            session,
            CostCenter,
            match={"company_id": company.id, "code": code},
            values={
                "name": name,
                "project_id": project_ids.get(project_code) if project_code else None,
            },
            result=result,
            protected_fields=("name",),
        )

    await session.flush()
    return result


# -----------------------------------------------------------------------------
# Users
# -----------------------------------------------------------------------------

# (email, phone, name, role code, scope type, scope code)
USERS: tuple[tuple[str, str, str, str, ScopeType, str | None], ...] = (
    ("admin@krb.example", "03001000001", "Sys Admin", "SUPER_ADMIN", ScopeType.GLOBAL, None),
    ("ceo@krb.example", "03001000002", "Kamran Rauf", "EXECUTIVE", ScopeType.COMPANY, None),
    (
        "finance@krb.example",
        "03001000003",
        "Nadia Hussain",
        "FINANCE_MANAGER",
        ScopeType.COMPANY,
        None,
    ),
    ("hr@krb.example", "03001000004", "Sana Iqbal", "HR_MANAGER", ScopeType.COMPANY, None),
    (
        "procurement@krb.example",
        "03001000005",
        "Ahmed Raza",
        "PROCUREMENT_MANAGER",
        ScopeType.COMPANY,
        None,
    ),
    (
        "pm.gvh@krb.example",
        "03001000006",
        "Bilal Ahmad",
        "PROJECT_MANAGER",
        ScopeType.PROJECT,
        "GVH",
    ),
    (
        "pm.rsd@krb.example",
        "03001000007",
        "Faisal Khan",
        "PROJECT_MANAGER",
        ScopeType.PROJECT,
        "RSD",
    ),
    (
        "sm.gvh1@krb.example",
        "03001000008",
        "Imran Shah",
        "SITE_MANAGER",
        ScopeType.SITE,
        "GVH-S1",
    ),
    (
        "store.cs@krb.example",
        "03001000009",
        "Zubair Ali",
        "STORE_MANAGER",
        ScopeType.SITE,
        "CS-LHR",
    ),
    (
        "staff.gvh1@krb.example",
        "03001000010",
        "Rashid Mehmood",
        "SITE_STAFF",
        ScopeType.SITE,
        "GVH-S1",
    ),
    (
        "staff.gvh2@krb.example",
        "03001000011",
        "Tariq Javed",
        "SITE_STAFF",
        ScopeType.SITE,
        "GVH-S2",
    ),
    (
        "accounts@krb.example",
        "03001000012",
        "Hina Malik",
        "ACCOUNTS_OFFICER",
        ScopeType.COMPANY,
        None,
    ),
    ("auditor@krb.example", "03001000013", "External Auditor", "AUDITOR", ScopeType.COMPANY, None),
)


async def reset_dev_passwords(session: AsyncSession, company: Company) -> SeedResult:
    """Put every seeded account back to DEV_PASSWORD, unlocked, no forced change.

    Exists so browser end-to-end runs are repeatable: `seed_users` protects
    password fields from reseeding (so an administrator's real password is
    never clobbered), which means one E2E run's password change would break
    the next. Refused outright in production by the caller.
    """
    result = SeedResult("dev password reset")
    password_hash = hash_password(DEV_PASSWORD)
    for email, *_ in USERS:
        user = (
            await session.execute(
                select(User).where(User.company_id == company.id, User.email == email)
            )
        ).scalar_one_or_none()
        if user is None:
            continue
        user.password_hash = password_hash
        user.must_change_password = False
        user.failed_login_count = 0
        user.locked_until = None
        user.status = UserStatus.ACTIVE.value
        result.updated += 1
    return result


async def seed_users(session: AsyncSession, company: Company) -> SeedResult:
    result = SeedResult("users & grants")
    password_hash = hash_password(DEV_PASSWORD)

    role_ids = {
        code: rid
        for rid, code in (
            await session.execute(select(Role.id, Role.code).where(Role.company_id == company.id))
        ).tuples()
    }
    project_ids = {
        code: pid
        for pid, code in (
            await session.execute(
                select(Project.id, Project.code).where(Project.company_id == company.id)
            )
        ).tuples()
    }
    site_ids = {
        code: sid
        for sid, code in (
            await session.execute(select(Site.id, Site.code).where(Site.company_id == company.id))
        ).tuples()
    }
    # `.all()` before dict(): a SQLAlchemy Result exposes keys(), so dict()
    # would treat the result itself as a mapping and try to subscript it.
    site_projects = dict(
        (
            await session.execute(
                select(Site.code, Site.project_id).where(Site.company_id == company.id)
            )
        )
        .tuples()
        .all()
    )

    for email, phone, name, role_code, scope_type, scope_code in USERS:
        default_project: UUID | None = None
        default_site: UUID | None = None
        scope_id: UUID | None = None

        if scope_type is ScopeType.PROJECT and scope_code:
            scope_id = project_ids[scope_code]
            default_project = scope_id
        elif scope_type is ScopeType.SITE and scope_code:
            scope_id = site_ids[scope_code]
            default_site = scope_id
            default_project = site_projects.get(scope_code)

        user = await upsert(
            session,
            User,
            match={"company_id": company.id, "email": email},
            values={
                "phone": normalise_phone(phone),
                "full_name": name,
                "password_hash": password_hash,
                # Seeded credentials must not survive into real use.
                "must_change_password": True,
                "password_changed_at": utcnow(),
                "status": UserStatus.ACTIVE.value,
                "default_project_id": default_project,
                "default_site_id": default_site,
                "locale": company.locale,
                "timezone": company.timezone,
            },
            result=result,
            protected_fields=(
                "password_hash",
                "must_change_password",
                "password_changed_at",
                "status",
                "full_name",
            ),
        )

        await upsert(
            session,
            UserRoleGrant,
            match={
                "user_id": user.id,
                "role_id": role_ids[role_code],
                "scope_type": scope_type.value,
                "scope_id": scope_id,
            },
            values={
                "company_id": company.id,
                "valid_from": date(2025, 7, 1),
                "grant_reason": "Seeded demo assignment",
            },
            result=result,
            protected_fields=("valid_from", "valid_to", "grant_reason"),
        )

    await session.flush()
    return result

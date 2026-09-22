"""Projects and sites, with scope filtering as the main subject.

These tests exist because of a real defect: `projects` has no `project_id`
column and `sites` has no `site_id` column — their own `id` *is* that
dimension — so a project-scoped user originally saw nothing at all. The
`__scope_self__` declaration fixes it, and these tests keep it fixed.

The second property under test is that granting a site-scoped user sight of
their parent project does not hand them the project's other sites.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.org.models import Project, Site

pytestmark = pytest.mark.integration

ADMIN = "admin@krb.example"
PM_GVH = "pm.gvh@krb.example"
PM_RSD = "pm.rsd@krb.example"
SITE_MANAGER = "sm.gvh1@krb.example"
SITE_STAFF = "staff.gvh1@krb.example"
AUDITOR = "auditor@krb.example"


class TestProjectScoping:
    async def test_a_global_grant_sees_every_project(self, api: AsyncClient, login: Any) -> None:
        headers = await login(ADMIN)
        body = (await api.get("/projects", headers=headers)).json()

        assert body["page"]["total"] == 3
        assert {item["code"] for item in body["items"]} == {"GVH", "RSD", "CCD"}

    async def test_a_project_scoped_grant_sees_only_that_project(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login(PM_GVH)
        body = (await api.get("/projects", headers=headers)).json()

        assert body["page"]["total"] == 1
        assert body["items"][0]["code"] == "GVH"

    async def test_two_project_managers_see_different_projects(
        self, api: AsyncClient, login: Any
    ) -> None:
        gvh = (await api.get("/projects", headers=await login(PM_GVH))).json()
        rsd = (await api.get("/projects", headers=await login(PM_RSD))).json()

        assert [i["code"] for i in gvh["items"]] == ["GVH"]
        assert [i["code"] for i in rsd["items"]] == ["RSD"]

    async def test_the_total_matches_the_filtered_rows(self, api: AsyncClient, login: Any) -> None:
        """Scope is a WHERE clause, so the count cannot disagree with the rows.

        A post-filter would report 3 and show 1, which is the failure mode this
        design exists to prevent.
        """
        headers = await login(PM_GVH)
        body = (await api.get("/projects", headers=headers)).json()

        assert body["page"]["total"] == len(body["items"])

    async def test_fetching_an_out_of_scope_project_is_404_not_403(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        """403 would confirm the record exists."""
        riverside = (await db.execute(select(Project).where(Project.code == "RSD"))).scalar_one()

        response = await api.get(f"/projects/{riverside.id}", headers=await login(PM_GVH))
        assert response.status_code == 404


class TestSiteScoping:
    async def test_a_project_grant_reaches_the_projects_sites(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login(PM_GVH)
        body = (await api.get("/sites", headers=headers)).json()

        assert {item["code"] for item in body["items"]} == {"GVH-S1", "GVH-S2"}

    async def test_a_site_grant_reaches_only_that_site(self, api: AsyncClient, login: Any) -> None:
        headers = await login(SITE_MANAGER)
        body = (await api.get("/sites", headers=headers)).json()

        assert [item["code"] for item in body["items"]] == ["GVH-S1"]

    async def test_a_site_grant_reveals_the_parent_project_record(
        self, api: AsyncClient, login: Any
    ) -> None:
        """A site manager must be able to see which project they are on."""
        headers = await login(SITE_MANAGER)
        body = (await api.get("/projects", headers=headers)).json()

        assert [item["code"] for item in body["items"]] == ["GVH"]

    async def test_parent_project_visibility_does_not_leak_sibling_sites(
        self, api: AsyncClient, login: Any
    ) -> None:
        """The whole reason parent_project_ids is separate from project_ids.

        Widening the site manager's project scope would have handed them
        GVH-S2 as well — a privilege escalation dressed up as convenience.
        """
        headers = await login(SITE_MANAGER)
        codes = {
            item["code"] for item in (await api.get("/sites", headers=headers)).json()["items"]
        }

        assert "GVH-S1" in codes
        assert "GVH-S2" not in codes

    async def test_company_level_sites_are_not_visible_to_site_staff(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login(SITE_STAFF)
        codes = {
            item["code"] for item in (await api.get("/sites", headers=headers)).json()["items"]
        }

        assert "CS-LHR" not in codes
        assert "HO" not in codes

    async def test_an_admin_sees_company_level_sites_with_no_project(
        self, api: AsyncClient, login: Any
    ) -> None:
        """The answer to the Phase 1 modelling question: sites need not belong
        to a project."""
        headers = await login(ADMIN)
        items = (await api.get("/sites", headers=headers)).json()["items"]

        central = next(item for item in items if item["code"] == "CS-LHR")
        assert central["project_id"] is None
        assert central["site_type"] == "CENTRAL_STORE"


class TestCreateProject:
    async def test_creates_the_eight_standard_phases(self, api: AsyncClient, login: Any) -> None:
        headers = await login(ADMIN)
        created = (
            await api.post(
                "/projects",
                headers=headers,
                json={
                    "code": "NHP",
                    "name": "New Horizon Phase 1",
                    "project_type": "HOUSING_SCHEME",
                    "centroid": {"latitude": 31.52, "longitude": 74.35},
                },
            )
        ).json()

        phases = (await api.get(f"/projects/{created['id']}/phases", headers=headers)).json()
        codes = [phase["code"] for phase in phases]

        assert len(codes) == 8
        assert codes[:3] == ["LAND_DEV", "EARTHWORKS", "ROADS"]
        assert "SEWERAGE" in codes and "ELECTRICAL" in codes

    async def test_a_new_project_starts_as_draft(self, api: AsyncClient, login: Any) -> None:
        headers = await login(ADMIN)
        body = (
            await api.post(
                "/projects", headers=headers, json={"code": "DRF", "name": "Draft Project"}
            )
        ).json()
        assert body["status"] == "DRAFT"

    async def test_latitude_and_longitude_survive_the_round_trip(
        self, api: AsyncClient, login: Any
    ) -> None:
        """PostGIS stores (lon, lat); humans say (lat, lon). Swapping them puts
        a Lahore site in the Indian Ocean without raising anything."""
        headers = await login(ADMIN)
        created = (
            await api.post(
                "/projects",
                headers=headers,
                json={
                    "code": "GEO",
                    "name": "Geo Round Trip",
                    "centroid": {"latitude": 31.5204, "longitude": 74.3587},
                },
            )
        ).json()

        assert created["centroid"]["latitude"] == pytest.approx(31.5204, abs=1e-6)
        assert created["centroid"]["longitude"] == pytest.approx(74.3587, abs=1e-6)

    async def test_end_before_start_is_refused(self, api: AsyncClient, login: Any) -> None:
        headers = await login(ADMIN)
        response = await api.post(
            "/projects",
            headers=headers,
            json={
                "code": "BAD",
                "name": "Backwards Dates",
                "start_date": "2027-01-01",
                "end_date": "2026-01-01",
            },
        )
        assert response.status_code == 422

    async def test_a_lowercase_code_is_refused(self, api: AsyncClient, login: Any) -> None:
        headers = await login(ADMIN)
        response = await api.post(
            "/projects", headers=headers, json={"code": "abc", "name": "Lowercase Code"}
        )
        assert response.status_code == 422


class TestProjectLifecycle:
    async def test_an_illegal_transition_is_refused(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        """DRAFT cannot jump straight to CLOSED."""
        headers = await login(ADMIN)
        ccd = (await db.execute(select(Project).where(Project.code == "CCD"))).scalar_one()

        response = await api.post(
            f"/projects/{ccd.id}/status",
            headers=headers,
            json={"status": "CLOSED", "reason": "Trying to skip the lifecycle"},
        )
        assert response.status_code == 409
        assert response.json()["current_status"] == "DRAFT"

    async def test_closing_requires_a_reason(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        headers = await login(ADMIN)
        ccd = (await db.execute(select(Project).where(Project.code == "CCD"))).scalar_one()

        response = await api.post(
            f"/projects/{ccd.id}/status", headers=headers, json={"status": "CANCELLED"}
        )
        assert response.status_code == 422

    async def test_a_project_with_sites_cannot_be_closed(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        headers = await login(ADMIN)
        gvh = (await db.execute(select(Project).where(Project.code == "GVH"))).scalar_one()

        await api.post(f"/projects/{gvh.id}/status", headers=headers, json={"status": "COMPLETED"})
        response = await api.post(
            f"/projects/{gvh.id}/status",
            headers=headers,
            json={"status": "CLOSED", "reason": "Handover complete"},
        )

        assert response.status_code == 422
        assert response.json()["rule"] == "project_has_open_sites"

    async def test_status_changes_are_audited(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        headers = await login(ADMIN)
        ccd = (await db.execute(select(Project).where(Project.code == "CCD"))).scalar_one()

        await api.post(f"/projects/{ccd.id}/status", headers=headers, json={"status": "ACTIVE"})

        rows = (
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == ccd.id, AuditLog.entity_type == "Project"
                    )
                )
            )
            .scalars()
            .all()
        )

        # A status change produces two complementary rows: the automatic
        # field diff (evidence) and the explicit business entry (narrative).
        summaries = [row.summary for row in rows if row.summary]
        assert any("DRAFT -> ACTIVE" in summary for summary in summaries), summaries

        # The CREATE row also lists `status` among its changed fields, so the
        # diff we want is specifically the UPDATE.
        diffs = [
            row for row in rows if row.action == "UPDATE" and "status" in (row.changed_fields or [])
        ]
        assert diffs, "the field-level diff must be recorded too"
        assert diffs[0].old_values == {"status": "DRAFT"}
        assert diffs[0].new_values == {"status": "ACTIVE"}


class TestSiteCreation:
    async def test_a_development_site_must_have_a_project(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login(ADMIN)
        response = await api.post(
            "/sites",
            headers=headers,
            json={"code": "ORPHAN", "name": "Orphan Site", "site_type": "DEVELOPMENT"},
        )

        assert response.status_code == 422
        message = " ".join(e["message"] for e in response.json()["errors"])
        assert "must belong to a project" in message

    async def test_a_central_store_needs_no_project(self, api: AsyncClient, login: Any) -> None:
        headers = await login(ADMIN)
        response = await api.post(
            "/sites",
            headers=headers,
            json={
                "code": "CS-ISB",
                "name": "Central Store — Islamabad",
                "site_type": "CENTRAL_STORE",
                "centroid": {"latitude": 33.6844, "longitude": 73.0479},
            },
        )

        assert response.status_code == 201
        assert response.json()["project_id"] is None


class TestGeofence:
    async def test_a_radius_geofence_round_trips(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        headers = await login(ADMIN)
        site = (await db.execute(select(Site).where(Site.code == "GVH-S1"))).scalar_one()

        response = await api.put(
            f"/sites/{site.id}/geofence",
            headers=headers,
            json={
                "centroid": {"latitude": 31.4110, "longitude": 74.2461},
                "geofence_radius_m": "750.00",
            },
        )
        assert response.status_code == 200

        fence = (await api.get(f"/sites/{site.id}/geofence", headers=headers)).json()
        assert fence["effective_mode"] == "RADIUS"
        assert fence["geofence_radius_m"] == "750.00"

    async def test_a_polygon_wins_over_a_radius(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        """A rectangular site is badly described by a circle."""
        headers = await login(ADMIN)
        site = (await db.execute(select(Site).where(Site.code == "GVH-S2"))).scalar_one()

        await api.put(
            f"/sites/{site.id}/geofence",
            headers=headers,
            json={
                "boundary": [
                    {"latitude": 31.4060, "longitude": 74.2490},
                    {"latitude": 31.4060, "longitude": 74.2515},
                    {"latitude": 31.4085, "longitude": 74.2515},
                    {"latitude": 31.4085, "longitude": 74.2490},
                ]
            },
        )

        fence = (await api.get(f"/sites/{site.id}/geofence", headers=headers)).json()
        assert fence["effective_mode"] == "POLYGON"
        # The ring is closed for us: four corners come back as five points.
        assert len(fence["boundary"]) == 5
        assert fence["boundary"][0] == fence["boundary"][-1]

    async def test_a_boundary_with_two_points_is_refused(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        headers = await login(ADMIN)
        site = (await db.execute(select(Site).where(Site.code == "GVH-S1"))).scalar_one()

        response = await api.put(
            f"/sites/{site.id}/geofence",
            headers=headers,
            json={
                "boundary": [
                    {"latitude": 31.41, "longitude": 74.24},
                    {"latitude": 31.42, "longitude": 74.25},
                ]
            },
        )
        assert response.status_code == 422

    async def test_changing_a_geofence_is_audited_as_a_rule_change(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        """Widening a geofence is how a delivery two kilometres away stops
        being flagged, so it has to be as visible as the flags it suppresses."""
        headers = await login(ADMIN)
        site = (await db.execute(select(Site).where(Site.code == "GVH-S1"))).scalar_one()

        await api.put(
            f"/sites/{site.id}/geofence",
            headers=headers,
            json={"geofence_radius_m": "2000.00"},
        )

        row = (
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == site.id, AuditLog.action == "RULE_CHANGE"
                    )
                )
            )
            .scalars()
            .first()
        )
        assert row is not None
        assert row.summary is not None
        assert "500.00m -> 2000.00m" in row.summary

    async def test_site_staff_cannot_change_a_geofence(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        """Otherwise the person whose entries get flagged could move the fence."""
        headers = await login(SITE_STAFF)
        site = (await db.execute(select(Site).where(Site.code == "GVH-S1"))).scalar_one()

        response = await api.put(
            f"/sites/{site.id}/geofence", headers=headers, json={"geofence_radius_m": "5000.00"}
        )
        assert response.status_code == 403


class TestAuditorReadOnly:
    async def test_can_read_projects_and_sites(self, api: AsyncClient, login: Any) -> None:
        headers = await login(AUDITOR)
        assert (await api.get("/projects", headers=headers)).status_code == 200
        assert (await api.get("/sites", headers=headers)).status_code == 200

    async def test_cannot_create_a_project(self, api: AsyncClient, login: Any) -> None:
        headers = await login(AUDITOR)
        response = await api.post(
            "/projects", headers=headers, json={"code": "AUD", "name": "Auditor Project"}
        )
        assert response.status_code == 403

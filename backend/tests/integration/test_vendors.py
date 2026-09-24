"""Vendors end to end.

The Phase 1 exit criterion: creating a vendor through the API writes a row to
PostgreSQL with an audit entry, an Auditor can read it and provably cannot
write it, and the state machine is enforced.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.vendors.models import Vendor, VendorContact
from app.platform.models import OutboxEvent

pytestmark = pytest.mark.integration

PROCUREMENT = "procurement@krb.example"
AUDITOR = "auditor@krb.example"
SITE_STAFF = "staff.gvh1@krb.example"
FINANCE = "finance@krb.example"

NEW_VENDOR: dict[str, Any] = {
    "legal_name": "Nawaz Brothers Crush Supply",
    "trade_name": "Nawaz Crush",
    "vendor_type": "MATERIAL_SUPPLIER",
    "ntn": "7654321-9",
    "is_filer": True,
    "payment_terms_days": 21,
    "phone": "+924235559999",
    "email": "sales@nawazcrush.example",
    "address": {"city": "Lahore", "country": "PK"},
    "contacts": [
        {
            "name": "Nawaz Ali",
            "designation": "Owner",
            "phone": "+923219999999",
            "is_primary": True,
        }
    ],
}


class TestListVendors:
    async def test_returns_the_seeded_vendors(self, api: AsyncClient, login: Any) -> None:
        headers = await login(PROCUREMENT)
        body = (await api.get("/vendors", headers=headers, params={"limit": 5})).json()

        assert body["page"]["total"] == 10
        assert len(body["items"]) == 5

    async def test_list_view_does_not_disclose_tax_or_bank_identifiers(
        self, api: AsyncClient, login: Any
    ) -> None:
        """A vendor grid is read by far more people than should see these."""
        headers = await login(PROCUREMENT)
        item = (await api.get("/vendors", headers=headers)).json()["items"][0]

        for leaked in ("ntn", "strn", "cnic", "iban", "account_no", "bank_accounts"):
            assert leaked not in item, leaked

    async def test_search_matches_name_and_code(self, api: AsyncClient, login: Any) -> None:
        headers = await login(PROCUREMENT)
        body = (await api.get("/vendors", headers=headers, params={"q": "Shree"})).json()

        assert body["page"]["total"] == 1
        assert "Shree" in body["items"][0]["display_name"]

    async def test_filter_by_type_repeats_as_or(self, api: AsyncClient, login: Any) -> None:
        headers = await login(PROCUREMENT)
        body = (
            await api.get(
                "/vendors",
                headers=headers,
                params=[("vendor_type", "CONTRACTOR"), ("vendor_type", "TRANSPORTER")],
            )
        ).json()

        types = {item["vendor_type"] for item in body["items"]}
        assert types == {"CONTRACTOR", "TRANSPORTER"}

    async def test_unknown_sort_field_is_refused(self, api: AsyncClient, login: Any) -> None:
        """Silently ignoring a sort is how someone exports the wrong data."""
        headers = await login(PROCUREMENT)
        response = await api.get("/vendors", headers=headers, params={"sort": "credit_limit"})

        assert response.status_code == 422
        assert response.json()["errors"][0]["field"] == "sort"

    async def test_total_is_consistent_with_the_filter(self, api: AsyncClient, login: Any) -> None:
        """The count must come from the same filtered query, not the whole table."""
        headers = await login(PROCUREMENT)
        body = (await api.get("/vendors", headers=headers, params={"q": "Sand", "limit": 1})).json()

        assert body["page"]["total"] == 1
        assert body["page"]["has_more"] is False


class TestCreateVendor:
    async def test_creates_a_row_with_a_server_allocated_code(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        headers = await login(PROCUREMENT)
        response = await api.post("/vendors", headers=headers, json=NEW_VENDOR)

        assert response.status_code == 201
        body = response.json()
        # The seeded vendors occupy VEN-00001..00010 and the counter was moved
        # past them, so the first allocated code is 11.
        assert body["code"] == "VEN-00011"

        row = (await db.execute(select(Vendor).where(Vendor.code == "VEN-00011"))).scalar_one()
        assert row.legal_name == "Nawaz Brothers Crush Supply"
        assert row.payment_terms_days == 21
        assert row.created_by_id is not None

    async def test_a_new_vendor_is_not_tradeable_until_approved(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login(PROCUREMENT)
        body = (await api.post("/vendors", headers=headers, json=NEW_VENDOR)).json()

        assert body["status"] == "DRAFT"
        assert body["is_tradeable"] is False

    async def test_whitespace_in_the_name_is_normalised(self, api: AsyncClient, login: Any) -> None:
        headers = await login(PROCUREMENT)
        payload = NEW_VENDOR | {"legal_name": "  Nawaz   Brothers   Supply  "}
        body = (await api.post("/vendors", headers=headers, json=payload)).json()

        assert body["legal_name"] == "Nawaz Brothers Supply"

    async def test_contacts_are_created(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        headers = await login(PROCUREMENT)
        created = (await api.post("/vendors", headers=headers, json=NEW_VENDOR)).json()

        contacts = (
            (
                await db.execute(
                    select(VendorContact).where(VendorContact.vendor_id == created["id"])
                )
            )
            .scalars()
            .all()
        )
        assert len(contacts) == 1
        assert contacts[0].name == "Nawaz Ali"
        assert contacts[0].is_primary is True

    async def test_a_vendor_without_a_tax_identity_is_refused(
        self, api: AsyncClient, login: Any
    ) -> None:
        """Withholding tax cannot be computed without an NTN or a CNIC."""
        headers = await login(PROCUREMENT)
        response = await api.post(
            "/vendors", headers=headers, json={"legal_name": "No Tax Id Traders"}
        )

        assert response.status_code == 422
        message = " ".join(e["message"] for e in response.json()["errors"])
        assert "NTN" in message and "CNIC" in message

    async def test_a_malformed_ntn_is_refused(self, api: AsyncClient, login: Any) -> None:
        headers = await login(PROCUREMENT)
        response = await api.post(
            "/vendors", headers=headers, json=NEW_VENDOR | {"ntn": "not-an-ntn"}
        )
        assert response.status_code == 422

    async def test_two_primary_contacts_are_refused(self, api: AsyncClient, login: Any) -> None:
        headers = await login(PROCUREMENT)
        payload = NEW_VENDOR | {
            "contacts": [
                {"name": "A", "phone": "+923001111111", "is_primary": True},
                {"name": "B", "phone": "+923002222222", "is_primary": True},
            ]
        }
        response = await api.post("/vendors", headers=headers, json=payload)
        assert response.status_code == 422

    async def test_a_duplicate_explicit_code_is_a_clear_field_error(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login(PROCUREMENT)
        response = await api.post(
            "/vendors", headers=headers, json=NEW_VENDOR | {"code": "VEN-00001"}
        )

        assert response.status_code == 409
        assert response.json()["errors"][0]["field"] == "code"


class TestAuditAndEvents:
    async def test_creating_a_vendor_writes_a_linkable_audit_row(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        """`entity_id` must be populated.

        The UUID is assigned at construction rather than at INSERT precisely so
        the audit writer, which runs on before_flush, can record it. Without
        that, the audit row cannot be linked to the record it describes.
        """
        headers = await login(PROCUREMENT)
        created = (await api.post("/vendors", headers=headers, json=NEW_VENDOR)).json()

        rows = (
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.entity_type == "Vendor",
                        AuditLog.entity_id == created["id"],
                        AuditLog.action == "CREATE",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].entity_label == "VEN-00011"
        assert rows[0].actor_user_id is not None
        assert "legal_name" in (rows[0].changed_fields or [])
        # A snapshot of who acted, so the trail still reads correctly after a
        # rename or role change. These columns existed but were never filled
        # in until the browser E2E suite looked at the audit row.
        assert rows[0].actor_name == "Ahmed Raza"
        assert rows[0].actor_roles == ["PROCUREMENT_MANAGER"]

    async def test_no_audit_row_lacks_its_entity_id(self, db: AsyncSession) -> None:
        """A regression guard over the whole seeded dataset."""
        orphans = await db.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.entity_id.is_(None))
        )
        assert orphans == 0

    async def test_approval_is_audited_with_a_readable_summary(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        headers = await login(PROCUREMENT)
        created = (await api.post("/vendors", headers=headers, json=NEW_VENDOR)).json()
        await api.post(
            f"/vendors/{created['id']}/approve",
            headers=headers,
            json={"note": "Site trial completed"},
        )

        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == created["id"], AuditLog.action == "APPROVE"
                )
            )
        ).scalar_one()

        assert row.summary is not None
        assert "DRAFT -> ACTIVE" in row.summary
        assert "Site trial completed" in row.summary
        # Explicit record() entries carry the same actor snapshot as diffs.
        assert row.actor_name == "Ahmed Raza"
        assert row.actor_roles == ["PROCUREMENT_MANAGER"]
        assert row.old_values == {"status": "DRAFT"}

    async def test_events_are_queued_in_the_outbox(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        headers = await login(PROCUREMENT)
        created = (await api.post("/vendors", headers=headers, json=NEW_VENDOR)).json()
        await api.post(f"/vendors/{created['id']}/approve", headers=headers, json={})

        events = (
            (
                await db.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.aggregate_id == created["id"])
                    .order_by(OutboxEvent.occurred_at)
                )
            )
            .scalars()
            .all()
        )
        assert [e.event_type for e in events] == ["vendor.created", "vendor.approved"]
        assert all(e.status == "PENDING" for e in events)

    async def test_a_failed_create_leaves_no_trace(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        """A rolled-back transaction must not leave an audit row or an event."""
        headers = await login(PROCUREMENT)
        before = await db.scalar(select(func.count()).select_from(AuditLog))

        response = await api.post(
            "/vendors", headers=headers, json=NEW_VENDOR | {"code": "VEN-00002"}
        )
        assert response.status_code == 409

        after = await db.scalar(select(func.count()).select_from(AuditLog))
        assert after == before


class TestStateMachine:
    async def test_approve_makes_the_vendor_tradeable(self, api: AsyncClient, login: Any) -> None:
        headers = await login(PROCUREMENT)
        created = (await api.post("/vendors", headers=headers, json=NEW_VENDOR)).json()

        approved = await api.post(f"/vendors/{created['id']}/approve", headers=headers, json={})
        assert approved.status_code == 200
        body = approved.json()
        assert body["status"] == "ACTIVE"
        assert body["is_tradeable"] is True
        assert body["approved_at"] is not None

    async def test_approving_twice_is_refused(self, api: AsyncClient, login: Any) -> None:
        headers = await login(PROCUREMENT)
        created = (await api.post("/vendors", headers=headers, json=NEW_VENDOR)).json()
        await api.post(f"/vendors/{created['id']}/approve", headers=headers, json={})

        again = await api.post(f"/vendors/{created['id']}/approve", headers=headers, json={})
        assert again.status_code == 409
        assert again.json()["current_status"] == "ACTIVE"

    async def test_suspension_requires_a_reason(self, api: AsyncClient, login: Any) -> None:
        headers = await login(PROCUREMENT)
        created = (await api.post("/vendors", headers=headers, json=NEW_VENDOR)).json()
        await api.post(f"/vendors/{created['id']}/approve", headers=headers, json={})

        no_reason = await api.post(f"/vendors/{created['id']}/suspend", headers=headers, json={})
        assert no_reason.status_code == 422

        with_reason = await api.post(
            f"/vendors/{created['id']}/suspend",
            headers=headers,
            json={"reason": "Repeated short deliveries in August"},
        )
        assert with_reason.status_code == 200
        assert with_reason.json()["status"] == "SUSPENDED"
        assert "short deliveries" in with_reason.json()["suspension_reason"]

    async def test_a_blacklisted_vendor_cannot_be_edited(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login(PROCUREMENT)
        created = (await api.post("/vendors", headers=headers, json=NEW_VENDOR)).json()
        await api.post(f"/vendors/{created['id']}/approve", headers=headers, json={})
        await api.post(
            f"/vendors/{created['id']}/suspend",
            headers=headers,
            json={"reason": "Supplied off-specification material", "blacklist": True},
        )

        edit = await api.patch(
            f"/vendors/{created['id']}", headers=headers, json={"payment_terms_days": 60}
        )
        assert edit.status_code == 422
        assert "blacklisted" in edit.json()["detail"].lower()

    async def test_a_vendor_created_without_a_tax_id_cannot_be_approved(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        """Guards a vendor that predates the schema rule."""
        headers = await login(PROCUREMENT)
        created = (await api.post("/vendors", headers=headers, json=NEW_VENDOR)).json()

        # Simulate legacy data by clearing the identifiers directly.
        row = (await db.execute(select(Vendor).where(Vendor.id == created["id"]))).scalar_one()
        row.ntn = None
        row.cnic = None
        await db.flush()

        response = await api.post(f"/vendors/{created['id']}/approve", headers=headers, json={})
        assert response.status_code == 422
        assert response.json()["rule"] == "vendor_tax_identity_missing"


class TestOptimisticConcurrency:
    async def test_a_stale_version_is_refused(self, api: AsyncClient, login: Any) -> None:
        headers = await login(PROCUREMENT)
        created = (await api.post("/vendors", headers=headers, json=NEW_VENDOR)).json()
        stale_version = created["version"]

        first = await api.patch(
            f"/vendors/{created['id']}",
            headers=headers | {"If-Match": str(stale_version)},
            json={"payment_terms_days": 45},
        )
        assert first.status_code == 200

        second = await api.patch(
            f"/vendors/{created['id']}",
            headers=headers | {"If-Match": str(stale_version)},
            json={"payment_terms_days": 60},
        )
        assert second.status_code == 409
        assert "changed by someone else" in second.json()["detail"]


class TestAuthorisation:
    async def test_auditor_can_read(self, api: AsyncClient, login: Any) -> None:
        headers = await login(AUDITOR)
        assert (await api.get("/vendors", headers=headers)).status_code == 200

    async def test_auditor_cannot_create(self, api: AsyncClient, login: Any) -> None:
        headers = await login(AUDITOR)
        response = await api.post("/vendors", headers=headers, json=NEW_VENDOR)

        assert response.status_code == 403
        assert response.json()["required_permission"] == "vendors.create"

    async def test_auditor_cannot_approve(self, api: AsyncClient, login: Any) -> None:
        headers = await login(AUDITOR)
        vendor_id = (await api.get("/vendors", headers=headers)).json()["items"][0]["id"]

        response = await api.post(f"/vendors/{vendor_id}/approve", headers=headers, json={})
        assert response.status_code == 403

    async def test_site_staff_can_read_but_not_create(self, api: AsyncClient, login: Any) -> None:
        """Site staff need to pick a vendor on the delivery form, nothing more."""
        headers = await login(SITE_STAFF)
        assert (await api.get("/vendors", headers=headers)).status_code == 200
        assert (await api.post("/vendors", headers=headers, json=NEW_VENDOR)).status_code == 403

    async def test_bank_details_need_their_own_permission(
        self, api: AsyncClient, login: Any
    ) -> None:
        procurement = await login(PROCUREMENT)
        vendor_id = (await api.get("/vendors", headers=procurement)).json()["items"][0]["id"]

        # Procurement can manage vendors but not where the money goes.
        denied = await api.get(f"/vendors/{vendor_id}/bank-accounts", headers=procurement)
        assert denied.status_code == 403

        finance = await login(FINANCE)
        allowed = await api.get(f"/vendors/{vendor_id}/bank-accounts", headers=finance)
        assert allowed.status_code == 200

    async def test_detail_hides_bank_accounts_without_the_permission(
        self, api: AsyncClient, login: Any
    ) -> None:
        procurement = await login(PROCUREMENT)
        vendor_id = (await api.get("/vendors", headers=procurement)).json()["items"][0]["id"]

        body = (await api.get(f"/vendors/{vendor_id}", headers=procurement)).json()
        assert body["bank_accounts"] is None

    async def test_unauthenticated_requests_are_refused(self, api: AsyncClient) -> None:
        assert (await api.get("/vendors")).status_code == 401
        assert (await api.post("/vendors", json=NEW_VENDOR)).status_code == 401


class TestBankAccounts:
    async def test_adding_an_account_is_audited_with_masked_values(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        finance = await login(FINANCE)
        vendor_id = (await api.get("/vendors", headers=finance)).json()["items"][0]["id"]

        response = await api.post(
            f"/vendors/{vendor_id}/bank-accounts",
            headers=finance,
            json={
                "account_title": "Shree Stone Crushing Company",
                "iban": "PK36SCBL0000001123456702",
                "bank_name": "Standard Chartered",
                "branch_name": "Gulberg",
                "is_primary": True,
            },
        )
        assert response.status_code == 201
        assert response.json()["masked_account"] == "****6702"

        row = (
            (await db.execute(select(AuditLog).where(AuditLog.action == "BANK_DETAILS_CHANGE")))
            .scalars()
            .first()
        )
        assert row is not None
        assert row.summary is not None
        # The full number must never appear in the audit log.
        assert "PK36SCBL0000001123456702" not in row.summary
        assert "****6702" in row.summary

    async def test_the_person_who_entered_an_account_cannot_verify_it(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        vendor_id = (await api.get("/vendors", headers=finance)).json()["items"][0]["id"]

        created = (
            await api.post(
                f"/vendors/{vendor_id}/bank-accounts",
                headers=finance,
                json={
                    "account_title": "Test Account",
                    "account_no": "0011223344",
                    "bank_name": "Meezan Bank",
                },
            )
        ).json()

        response = await api.post(f"/vendors/bank-accounts/{created['id']}/verify", headers=finance)
        assert response.status_code == 422
        assert response.json()["rule"] == "self_verification"

"""Default approval workflows.

Seeded only when a document type has no workflow at all: once an
administrator has published their own, re-running the seeder must never
replace it (a seeder that silently rewrites who approves what would be a
control failure of its own).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import system_access_context
from app.modules.approvals.models import ApprovalWorkflow
from app.modules.approvals.services import workflow_service
from app.modules.org.models import Company

# Importing the service registers the purchase-request approval handler.
from app.modules.procurement.services import purchase_requests
from app.seeds.registry import SeedResult

_PM = {
    "name": "Project manager",
    "approver_type": "DYNAMIC",
    "approver_ref": "project_manager",
    "quorum_type": "ANY",
    "sla_hours": 24,
    # With no project manager assigned, the request goes to procurement
    # rather than failing to submit.
    "escalate_to_type": "ROLE",
    "escalate_to_ref": "PROCUREMENT_MANAGER",
}
_PROCUREMENT = {
    "name": "Procurement manager",
    "approver_type": "ROLE",
    "approver_ref": "PROCUREMENT_MANAGER",
    "quorum_type": "ANY",
    "sla_hours": 24,
    "escalate_to_type": "ROLE",
    "escalate_to_ref": "FINANCE_MANAGER",
}
_FINANCE = {
    "name": "Finance manager",
    "approver_type": "ROLE",
    "approver_ref": "FINANCE_MANAGER",
    "quorum_type": "ANY",
    "sla_hours": 48,
}
_EXECUTIVE = {
    "name": "Executive",
    "approver_type": "ROLE",
    "approver_ref": "EXECUTIVE",
    "quorum_type": "ANY",
    "sla_hours": 72,
}


def _steps(*steps: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**step, "step_no": i} for i, step in enumerate(steps, start=1)]


# The §25 worked example (docs/04 §1): 100 000 and 1 000 000 tiers.
PURCHASE_REQUEST_WORKFLOW: dict[str, Any] = {
    "rules": [
        {
            "sequence": 10,
            "name": "Under 100,000",
            "condition": {"<": [{"var": "total_amount"}, 100000]},
            "steps": _steps(_PM),
        },
        {
            "sequence": 20,
            "name": "100,000 to under 1,000,000",
            "condition": {"<": [{"var": "total_amount"}, 1000000]},
            "steps": _steps(_PM, _PROCUREMENT),
        },
        {
            "sequence": 99,
            "name": "1,000,000 and above",
            "condition": True,
            "steps": _steps(_PM, _PROCUREMENT, _FINANCE, _EXECUTIVE),
        },
    ]
}


async def seed_workflows(session: AsyncSession, company: Company) -> SeedResult:
    result = SeedResult("approval workflows")
    existing = await session.scalar(
        select(ApprovalWorkflow.id).where(
            ApprovalWorkflow.company_id == company.id,
            ApprovalWorkflow.doc_type == purchase_requests.DOC_TYPE,
        )
    )
    if existing is not None:
        result.skipped += 1
        return result

    ctx = system_access_context(company.id, UUID(int=0))
    row = await workflow_service.save_version(
        session,
        ctx,
        doc_type=purchase_requests.DOC_TYPE,
        name="Purchase request approval",
        description="Default three-tier chain from the requirements (§25).",
        definition=PURCHASE_REQUEST_WORKFLOW,
        change_note="Seeded default",
    )
    # A seeded row has no human author; the placeholder id must not linger.
    row.created_by_id = None
    result.created += 1
    return result

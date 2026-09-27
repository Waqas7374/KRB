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

# Importing the service registers the journal-entry approval handler.
from app.modules.finance.services import journal_entries
from app.modules.org.models import Company

# Importing the service registers the purchase-request approval handler.
from app.modules.procurement.services import purchase_orders, purchase_requests

# Importing the service registers the vendor-rate approval handler.
from app.modules.rates.services import rate_service

# Importing the service registers the stock-adjustment approval handler.
from app.modules.stock.services import adjustments
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


# Purchase orders commit money, so the first tier is procurement rather than the
# project manager, and an order the procurement manager raised themselves goes to
# finance (the step's escalation target) instead of approving itself.
_PO_PROCUREMENT = {**_PROCUREMENT, "escalate_to_ref": "FINANCE_MANAGER"}

PURCHASE_ORDER_WORKFLOW: dict[str, Any] = {
    "rules": [
        {
            "sequence": 10,
            "name": "Under 500,000",
            "condition": {"<": [{"var": "total_amount"}, 500000]},
            "steps": _steps(_PO_PROCUREMENT),
        },
        {
            "sequence": 20,
            "name": "500,000 to under 5,000,000",
            "condition": {"<": [{"var": "total_amount"}, 5000000]},
            "steps": _steps(_PO_PROCUREMENT, _FINANCE),
        },
        {
            "sequence": 99,
            "name": "5,000,000 and above",
            "condition": True,
            "steps": _steps(_PO_PROCUREMENT, _FINANCE, _EXECUTIVE),
        },
    ]
}

# A vendor's price is money the company will pay for a long time, so a
# meaningful change is signed by finance. A first rate (nothing to compare
# with) and a change within +/-10 % approve themselves, recorded as such; the
# thresholds are the administrator's to change.
# When finance itself proposes the change it cannot sign its own, so it goes up.
_RATE_FINANCE = {**_FINANCE, "escalate_to_type": "ROLE", "escalate_to_ref": "EXECUTIVE"}

VENDOR_RATE_WORKFLOW: dict[str, Any] = {
    "rules": [
        {
            "sequence": 10,
            "name": "First rate, or within 10 %",
            "condition": {
                "or": [
                    {"==": [{"var": "is_first_rate"}, True]},
                    {"between": [{"var": "change_pct"}, -10, 10]},
                ]
            },
            "steps": [],
        },
        {
            "sequence": 99,
            "name": "More than 10 % either way",
            "condition": True,
            "steps": _steps(_RATE_FINANCE),
        },
    ]
}

# A stock adjustment creates or destroys stock without a receipt or an issue behind
# it, so nothing about it approves itself: the project manager signs a small one,
# and anything from 25,000 up is signed by finance as well. When the project
# manager raised it, it goes up to finance rather than to themselves.
_ADJUSTMENT_PM = {**_PM, "escalate_to_ref": "FINANCE_MANAGER"}

STOCK_ADJUSTMENT_WORKFLOW: dict[str, Any] = {
    "rules": [
        {
            "sequence": 10,
            "name": "Under 25,000",
            "condition": {"<": [{"var": "value_abs"}, 25000]},
            "steps": _steps(_ADJUSTMENT_PM),
        },
        {
            "sequence": 99,
            "name": "25,000 and above",
            "condition": True,
            "steps": _steps(_ADJUSTMENT_PM, _FINANCE),
        },
    ]
}

# A manual journal entry is the one posting that is not the automatic result of
# an already-approved document, so it goes through finance's own signature: under
# 100,000 finance alone, at or above it finance plus the executive. When finance
# itself raises the entry, the finance step has nobody eligible to act (a person
# cannot approve their own step) and escalates to the executive on its SLA.
_JE_FINANCE = {**_FINANCE, "escalate_to_type": "ROLE", "escalate_to_ref": "EXECUTIVE"}

JOURNAL_ENTRY_WORKFLOW: dict[str, Any] = {
    "rules": [
        {
            "sequence": 10,
            "name": "Below 100,000",
            "condition": {"<": [{"var": "amount_abs"}, 100000]},
            "steps": _steps(_JE_FINANCE),
        },
        {
            "sequence": 99,
            "name": "100,000 or more",
            "condition": True,
            "steps": _steps(_JE_FINANCE, _EXECUTIVE),
        },
    ]
}

_DEFAULTS = (
    (
        purchase_requests.DOC_TYPE,
        "Purchase request approval",
        "Default three-tier chain from the requirements (§25).",
        PURCHASE_REQUEST_WORKFLOW,
    ),
    (
        purchase_orders.DOC_TYPE,
        "Purchase order approval",
        "Default three-tier chain by order value. Edit it to match how KRB signs.",
        PURCHASE_ORDER_WORKFLOW,
    ),
    (
        rate_service.DOC_TYPE,
        "Vendor rate approval",
        "Small changes and first rates approve themselves; larger ones go to finance.",
        VENDOR_RATE_WORKFLOW,
    ),
    (
        journal_entries.DOC_TYPE,
        "Journal entry approval",
        "Every manual entry is signed by finance; 100,000 and above also needs the executive.",
        JOURNAL_ENTRY_WORKFLOW,
    ),
    (
        adjustments.DOC_TYPE,
        "Stock adjustment approval",
        "Every adjustment is signed: the project manager, plus finance from 25,000 up.",
        STOCK_ADJUSTMENT_WORKFLOW,
    ),
)


async def seed_workflows(session: AsyncSession, company: Company) -> SeedResult:
    result = SeedResult("approval workflows")
    ctx = system_access_context(company.id, UUID(int=0))
    for doc_type, name, description, definition in _DEFAULTS:
        existing = await session.scalar(
            select(ApprovalWorkflow.id).where(
                ApprovalWorkflow.company_id == company.id,
                ApprovalWorkflow.doc_type == doc_type,
            )
        )
        if existing is not None:
            result.skipped += 1
            continue
        row = await workflow_service.save_version(
            session,
            ctx,
            doc_type=doc_type,
            name=name,
            description=description,
            definition=definition,
            change_note="Seeded default",
        )
        # A seeded row has no human author; the placeholder id must not linger.
        row.created_by_id = None
        result.created += 1
    return result

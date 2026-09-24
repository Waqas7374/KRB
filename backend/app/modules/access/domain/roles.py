"""Standard role definitions.

Seeded as system roles. All except Super Administrator are editable, so a
customer can tighten or loosen a role without a code change — but the shipped
defaults encode the separation of duties the ERP depends on:

* nobody both raises and approves the same document by default;
* HR clerks cannot see pay;
* the Auditor has no write permission at all;
* bank-detail changes are a permission of their own.

Patterns ending in `*` expand against the permission catalogue.
"""

from __future__ import annotations

import fnmatch
from typing import Final, NamedTuple

from app.modules.access.domain.enums import ScopeType
from app.modules.access.domain.permissions import (
    PERMISSION_CODES,
    READ_ONLY_CODES,
    RESTRICTED_CODES,
)


class RoleDef(NamedTuple):
    code: str
    name: str
    description: str
    permissions: tuple[str, ...]
    allowed_scopes: tuple[ScopeType, ...]
    is_locked: bool = False

    def resolve(self) -> frozenset[str]:
        """Expand glob patterns against the catalogue."""
        resolved: set[str] = set()
        for pattern in self.permissions:
            if pattern == "*":
                resolved |= PERMISSION_CODES
            elif "*" in pattern:
                matched = {c for c in PERMISSION_CODES if fnmatch.fnmatchcase(c, pattern)}
                if not matched:
                    raise ValueError(f"Role {self.code}: pattern '{pattern}' matched nothing")
                resolved |= matched
            else:
                if pattern not in PERMISSION_CODES:
                    raise ValueError(f"Role {self.code}: unknown permission '{pattern}'")
                resolved.add(pattern)
        return frozenset(resolved)

    @property
    def is_read_only(self) -> bool:
        return self.resolve() <= READ_ONLY_CODES | {"audit.view", "approvals.view"}


_PROJECT_SCOPES: Final = (ScopeType.COMPANY, ScopeType.PROJECT)
_SITE_SCOPES: Final = (ScopeType.PROJECT, ScopeType.SITE)

# Every read permission — the Auditor's and the Executive's base.
_ALL_READS: Final = tuple(sorted(READ_ONLY_CODES))


STANDARD_ROLES: Final[tuple[RoleDef, ...]] = (
    RoleDef(
        code="SUPER_ADMIN",
        name="Super Administrator",
        description="Unrestricted access. Should be held by one or two people only.",
        permissions=("*",),
        allowed_scopes=(ScopeType.GLOBAL,),
        is_locked=True,
    ),
    RoleDef(
        code="EXECUTIVE",
        name="CEO / Executive",
        description=(
            "Executive dashboards and every read, plus final approval on high-value documents."
        ),
        permissions=(
            *_ALL_READS,
            "approvals.view",
            "procurement.pr.approve",
            "procurement.po.approve",
            "finance.budget.approve",
            "finance.payment.approve",
            "projects.close",
        ),
        allowed_scopes=(ScopeType.COMPANY,),
    ),
    RoleDef(
        code="FINANCE_MANAGER",
        name="Finance Manager",
        description="Owns the ledger, payables, budgets and period close.",
        permissions=(
            "finance.*",
            "reports.*",
            "audit.view",
            "approvals.view",
            "vendors.view",
            "vendors.manage_bank_details",
            "procurement.po.view",
            "procurement.po.view_pricing",
            "procurement.pr.approve",
            "inventory.view",
            "inventory.view_valuation",
            "materials.view",
            "units.view",
            "rates.view",
            "rates.view_history",
            "projects.view",
            "sites.view",
            "departments.view",
            "grn.view",
            "deliveries.view",
            "attachments.view",
            "attachments.upload",
            "attachments.delete",
            "notifications.view_own",
            "settings.view",
        ),
        allowed_scopes=(ScopeType.COMPANY,),
    ),
    RoleDef(
        code="HR_MANAGER",
        name="HR Manager",
        description="Owns employees, attendance, leave and payroll, including compensation.",
        permissions=(
            "hr.*",
            "users.view",
            "users.create",
            "users.update",
            "users.deactivate",
            "users.reset_password",
            "departments.view",
            "departments.manage",
            "projects.view",
            "sites.view",
            "reports.view",
            "reports.export",
            "approvals.view",
            "attachments.view",
            "attachments.upload",
            "attachments.delete",
            "notifications.view_own",
        ),
        allowed_scopes=(ScopeType.COMPANY,),
    ),
    RoleDef(
        code="PROCUREMENT_MANAGER",
        name="Procurement Manager",
        description="Runs sourcing end to end: requests, RFQs, quotations, orders and rates.",
        permissions=(
            "procurement.*",
            "vendors.view",
            "vendors.create",
            "vendors.update",
            "vendors.approve",
            "vendors.suspend",
            "rates.*",
            "materials.view",
            "materials.create",
            "materials.update",
            "materials.deactivate",
            # Conversion factors price every delivery (§20), the same way
            # rates do; the role that owns pricing owns both.
            "units.view",
            "units.manage",
            "units.manage_conversions",
            "warehouses.view",
            "projects.view",
            "sites.view",
            "grn.view",
            "deliveries.view",
            "inventory.view",
            "reports.view",
            "reports.export",
            "approvals.view",
            "attachments.view",
            "attachments.upload",
            "attachments.delete",
            "notifications.view_own",
        ),
        allowed_scopes=(ScopeType.COMPANY,),
    ),
    RoleDef(
        code="PROJECT_MANAGER",
        name="Project Manager",
        description="Owns delivery of one or more projects: budget, procurement and site work.",
        permissions=(
            "projects.view",
            "projects.update",
            "sites.view",
            "sites.create",
            "sites.update",
            "sites.manage_geofence",
            "procurement.pr.*",
            "procurement.rfq.view",
            "procurement.quotation.view",
            "procurement.po.view",
            "procurement.po.view_pricing",
            "procurement.po.approve",
            "deliveries.view",
            "deliveries.review",
            "deliveries.approve",
            "deliveries.reject",
            "deliveries.request_correction",
            "deliveries.export",
            "grn.view",
            "grn.create",
            "grn.approve",
            "inventory.view",
            "inventory.view_valuation",
            "inventory.issue",
            "inventory.transfer",
            "inventory.adjust",
            "finance.budget.view",
            "materials.view",
            "units.view",
            "vendors.view",
            "rates.view",
            "hr.attendance.view",
            "hr.attendance.approve",
            "hr.leave.view",
            "hr.leave.approve",
            "hr.employee.view",
            "reports.view",
            "reports.export",
            "approvals.view",
            "attachments.view",
            "attachments.upload",
            "attachments.delete",
            "notifications.view_own",
        ),
        allowed_scopes=_PROJECT_SCOPES,
    ),
    RoleDef(
        code="SITE_MANAGER",
        name="Site Manager",
        description="Runs one site: receiving, verification, stock and attendance.",
        permissions=(
            "sites.view",
            "projects.view",
            "deliveries.view",
            "deliveries.create",
            "deliveries.update_draft",
            "deliveries.submit",
            "deliveries.review",
            "deliveries.approve",
            "deliveries.reject",
            "deliveries.request_correction",
            "deliveries.export",
            "grn.view",
            "grn.create",
            "inventory.view",
            "inventory.issue",
            "inventory.transfer",
            "inventory.adjust",
            "materials.view",
            "units.view",
            "vendors.view",
            "rates.view",
            "procurement.pr.view",
            "procurement.pr.create",
            "procurement.pr.submit",
            "procurement.po.view",
            "hr.attendance.*",
            "hr.leave.view",
            "reports.view",
            "approvals.view",
            "attachments.view",
            "attachments.upload",
            "attachments.delete",
            "notifications.view_own",
        ),
        allowed_scopes=_SITE_SCOPES,
    ),
    RoleDef(
        code="STORE_MANAGER",
        name="Store Manager",
        description="Owns the store: GRNs, stock movements, issues and adjustments.",
        permissions=(
            "inventory.*",
            "grn.*",
            "warehouses.view",
            "warehouses.manage",
            "materials.view",
            "units.view",
            "deliveries.view",
            "vendors.view",
            "procurement.po.view",
            "projects.view",
            "sites.view",
            "reports.view",
            "reports.export",
            "approvals.view",
            "attachments.view",
            "attachments.upload",
            "attachments.delete",
            "notifications.view_own",
        ),
        allowed_scopes=_SITE_SCOPES,
    ),
    RoleDef(
        code="SITE_STAFF",
        name="Site Staff",
        description=(
            "Mobile-only: records incoming truck deliveries at an assigned site. "
            "Cannot approve anything, including their own entries."
        ),
        permissions=(
            "deliveries.view",
            "deliveries.create",
            "deliveries.update_draft",
            "deliveries.submit",
            "materials.view",
            "units.view",
            "vendors.view",
            "sites.view",
            "projects.view",
            "procurement.po.view",
            "attachments.upload",
            "attachments.view",
            "notifications.view_own",
            "hr.attendance.mark",
            "hr.leave.request",
            "hr.leave.view",
        ),
        allowed_scopes=(ScopeType.SITE,),
    ),
    RoleDef(
        code="ACCOUNTS_OFFICER",
        name="Accounts Officer",
        description="Day-to-day accounting: invoices, receipts and journal preparation.",
        permissions=(
            "finance.ap.view",
            "finance.ap.create",
            "finance.ap.match",
            "finance.ar.view",
            "finance.ar.create",
            "finance.ar.receipt",
            "finance.gl.view",
            "finance.gl.create",
            "finance.coa.view",
            "finance.payment.view",
            "finance.payment.request",
            "finance.budget.view",
            "vendors.view",
            "procurement.po.view",
            "procurement.po.view_pricing",
            "grn.view",
            "deliveries.view",
            "inventory.view",
            "materials.view",
            "units.view",
            "rates.view",
            "projects.view",
            "sites.view",
            "reports.view",
            "reports.view_financial",
            "reports.export",
            "approvals.view",
            "attachments.view",
            "attachments.upload",
            "attachments.delete",
            "notifications.view_own",
        ),
        allowed_scopes=(ScopeType.COMPANY,),
    ),
    RoleDef(
        code="AUDITOR",
        name="Auditor",
        description=(
            "Read-only across the whole system, including the audit log. "
            "Holds no write permission of any kind."
        ),
        permissions=(*_ALL_READS, "audit.view", "approvals.view"),
        allowed_scopes=(ScopeType.COMPANY,),
    ),
)

ROLES_BY_CODE: Final[dict[str, RoleDef]] = {r.code: r for r in STANDARD_ROLES}

SUPER_ADMIN_CODE: Final = "SUPER_ADMIN"
AUDITOR_CODE: Final = "AUDITOR"


def validate_catalogue() -> None:
    """Fail loudly at import/seed time on a typo or an unreachable permission.

    Also asserts the two invariants the design depends on: the Auditor can write
    nothing, and restricted permissions belong to the Super Administrator alone.
    """
    for role in STANDARD_ROLES:
        role.resolve()

    auditor_writes = (
        ROLES_BY_CODE[AUDITOR_CODE].resolve()
        - READ_ONLY_CODES
        - {
            "audit.view",
            "approvals.view",
        }
    )
    if auditor_writes:
        raise ValueError(f"Auditor role holds write permissions: {sorted(auditor_writes)}")

    for role in STANDARD_ROLES:
        if role.code == SUPER_ADMIN_CODE:
            continue
        leaked = role.resolve() & RESTRICTED_CODES
        if leaked:
            raise ValueError(
                f"Role {role.code} holds restricted permissions {sorted(leaked)}; "
                "those belong to the Super Administrator only."
            )

"""The permission catalogue.

The single source of truth for what this system can authorise. Seeding inserts
these rows; a test asserts the database and this file agree, so a permission can
never be referenced in code without existing in the catalogue, and an orphaned
row can never linger after a permission is removed.

Naming: `module.action`. `.view` covers list and detail. `.approve` never
implies `.create` — the person who raises a purchase request and the person who
approves it are different people, and the permission model has to say so.
"""

from __future__ import annotations

from typing import Final, NamedTuple


class PermissionDef(NamedTuple):
    code: str
    description: str
    restricted: bool = False

    @property
    def module(self) -> str:
        return self.code.split(".", 1)[0]

    @property
    def action(self) -> str:
        return self.code.split(".", 1)[1]

    @property
    def is_read(self) -> bool:
        """Whether holding this permission alone can change no data."""
        last = self.code.rsplit(".", 1)[-1]
        return last in {
            "view",
            "view_own",
            "view_salary",
            "view_financial",
            "view_valuation",
            "view_history",
            "export",
        }


# -----------------------------------------------------------------------------
# Catalogue
# -----------------------------------------------------------------------------

P: Final[tuple[PermissionDef, ...]] = (
    # --- Organisation -------------------------------------------------------
    PermissionDef("projects.view", "View projects and their phases"),
    PermissionDef("projects.create", "Create projects"),
    PermissionDef("projects.update", "Edit project details, phases and settings"),
    PermissionDef("projects.close", "Close or cancel a project"),
    PermissionDef("sites.view", "View sites"),
    PermissionDef("sites.create", "Create sites"),
    PermissionDef("sites.update", "Edit site details"),
    PermissionDef("sites.manage_geofence", "Set a site's geofence boundary or radius"),
    PermissionDef("departments.view", "View departments and cost centres"),
    PermissionDef("departments.manage", "Create and edit departments and cost centres"),
    # --- Users & access -----------------------------------------------------
    PermissionDef("users.view", "View user accounts"),
    PermissionDef("users.create", "Invite and create users"),
    PermissionDef("users.update", "Edit user details"),
    PermissionDef("users.deactivate", "Deactivate or suspend a user"),
    PermissionDef("users.assign_roles", "Grant and revoke roles", restricted=False),
    PermissionDef("users.reset_password", "Force a password reset for another user"),
    PermissionDef("users.manage_devices", "View and revoke a user's mobile devices"),
    PermissionDef("roles.view", "View roles and the permission catalogue"),
    PermissionDef("roles.manage", "Create roles and change their permissions", restricted=True),
    # --- Master data --------------------------------------------------------
    PermissionDef("materials.view", "View the material master"),
    PermissionDef("materials.create", "Add materials and categories"),
    PermissionDef("materials.update", "Edit materials and categories"),
    PermissionDef("materials.deactivate", "Deactivate a material"),
    PermissionDef("units.view", "View units and conversion factors"),
    PermissionDef("units.manage", "Create and edit units"),
    PermissionDef(
        "units.manage_conversions",
        "Create and supersede unit conversion factors — changes how deliveries are priced",
    ),
    PermissionDef("warehouses.view", "View warehouses"),
    PermissionDef("warehouses.manage", "Create and edit warehouses"),
    # --- Vendors ------------------------------------------------------------
    PermissionDef("vendors.view", "View vendors"),
    PermissionDef("vendors.create", "Create vendors"),
    PermissionDef("vendors.update", "Edit vendor details and contacts"),
    PermissionDef("vendors.approve", "Approve a vendor for trading"),
    PermissionDef("vendors.suspend", "Suspend or blacklist a vendor"),
    PermissionDef(
        "vendors.manage_bank_details",
        "View and change vendor bank accounts — a high-risk payment-diversion surface",
    ),
    # --- Vendor rates -------------------------------------------------------
    PermissionDef("rates.view", "View current vendor rates"),
    PermissionDef("rates.create", "Create a new vendor rate period"),
    PermissionDef("rates.approve", "Approve a vendor rate change"),
    PermissionDef("rates.view_history", "View the full rate change history"),
    # --- Procurement --------------------------------------------------------
    PermissionDef("procurement.pr.view", "View purchase requests"),
    PermissionDef("procurement.pr.create", "Raise purchase requests"),
    PermissionDef("procurement.pr.submit", "Submit a purchase request for approval"),
    PermissionDef("procurement.pr.approve", "Approve or reject purchase requests"),
    PermissionDef("procurement.rfq.view", "View RFQs"),
    PermissionDef("procurement.rfq.create", "Create RFQs and invite vendors"),
    PermissionDef("procurement.rfq.issue", "Issue an RFQ to invited vendors"),
    PermissionDef("procurement.quotation.view", "View quotations and comparisons"),
    PermissionDef("procurement.quotation.record", "Record a vendor quotation"),
    PermissionDef(
        "procurement.quotation.select", "Select a winning quotation and record the reason"
    ),
    PermissionDef("procurement.po.view", "View purchase orders"),
    PermissionDef("procurement.po.create", "Create purchase orders"),
    PermissionDef("procurement.po.approve", "Approve purchase orders"),
    PermissionDef("procurement.po.send", "Send an approved PO to the vendor"),
    PermissionDef("procurement.po.amend", "Amend an approved purchase order"),
    PermissionDef("procurement.po.cancel", "Cancel or close a purchase order"),
    PermissionDef("procurement.po.view_pricing", "See rates and amounts on purchase orders"),
    # --- Deliveries ---------------------------------------------------------
    PermissionDef("deliveries.view", "View material deliveries"),
    PermissionDef("deliveries.create", "Record a material delivery"),
    PermissionDef("deliveries.update_draft", "Edit a delivery that has not been submitted"),
    PermissionDef("deliveries.submit", "Submit a delivery"),
    PermissionDef("deliveries.review", "Review flagged deliveries"),
    PermissionDef("deliveries.approve", "Approve deliveries"),
    PermissionDef("deliveries.reject", "Reject deliveries"),
    PermissionDef("deliveries.request_correction", "Send a delivery back for correction"),
    PermissionDef("deliveries.reopen", "Reopen a delivery that reached a terminal status"),
    PermissionDef("deliveries.waive_flag", "Waive a delivery flag"),
    PermissionDef("deliveries.export", "Export delivery data"),
    # --- GRN & inventory ----------------------------------------------------
    PermissionDef("grn.view", "View goods received notes"),
    PermissionDef("grn.create", "Create a GRN from a delivery or counter purchase"),
    PermissionDef("grn.approve", "Approve a GRN — moves stock and posts to the ledger"),
    PermissionDef("grn.cancel", "Cancel a GRN by reversal"),
    PermissionDef("inventory.view", "View stock balances and the stock ledger"),
    PermissionDef("inventory.view_valuation", "See stock values and unit costs"),
    PermissionDef("inventory.issue", "Issue material from a store"),
    PermissionDef("inventory.transfer", "Transfer stock between sites"),
    PermissionDef("inventory.adjust", "Raise a stock adjustment"),
    PermissionDef("inventory.approve_adjustment", "Approve a stock adjustment"),
    # --- Finance ------------------------------------------------------------
    PermissionDef("finance.coa.view", "View the chart of accounts"),
    PermissionDef("finance.coa.manage", "Create and edit accounts and posting rules"),
    PermissionDef("finance.gl.view", "View the general ledger and trial balance"),
    PermissionDef("finance.gl.create", "Create journal entries"),
    PermissionDef("finance.gl.post", "Post journal entries"),
    PermissionDef("finance.gl.reverse", "Reverse a posted journal entry"),
    PermissionDef("finance.period.close", "Open and close accounting periods"),
    PermissionDef("finance.ap.view", "View vendor invoices and payables"),
    PermissionDef("finance.ap.create", "Enter vendor invoices"),
    PermissionDef("finance.ap.match", "Run PO / GRN / invoice matching"),
    PermissionDef("finance.ap.approve", "Approve or dispute a vendor invoice"),
    PermissionDef("finance.payment.view", "View payments"),
    PermissionDef("finance.payment.request", "Raise a payment request"),
    PermissionDef("finance.payment.approve", "Approve a payment request"),
    PermissionDef("finance.payment.execute", "Execute and allocate a payment"),
    PermissionDef("finance.ar.view", "View customers and receivables"),
    PermissionDef("finance.ar.create", "Create customer invoices"),
    PermissionDef("finance.ar.receipt", "Record customer receipts"),
    PermissionDef("finance.budget.view", "View budgets, commitments and variance"),
    PermissionDef("finance.budget.create", "Create and revise budgets"),
    PermissionDef("finance.budget.approve", "Approve a budget"),
    # --- HR -----------------------------------------------------------------
    PermissionDef("hr.employee.view", "View employee records"),
    PermissionDef("hr.employee.create", "Create employee records"),
    PermissionDef("hr.employee.update", "Edit employee records"),
    PermissionDef(
        "hr.employee.view_salary",
        "See compensation — separate from hr.employee.view so a clerk maintaining "
        "addresses cannot see pay",
    ),
    PermissionDef("hr.attendance.view", "View attendance"),
    PermissionDef("hr.attendance.mark", "Record attendance"),
    PermissionDef("hr.attendance.approve", "Approve attendance corrections and overtime"),
    PermissionDef("hr.leave.view", "View leave requests and balances"),
    PermissionDef("hr.leave.request", "Request leave"),
    PermissionDef("hr.leave.approve", "Approve or reject leave"),
    PermissionDef("hr.payroll.view", "View payroll runs and payslips"),
    PermissionDef("hr.payroll.process", "Process a payroll period"),
    PermissionDef("hr.payroll.approve", "Approve a payroll period for payment"),
    # --- Cross-cutting ------------------------------------------------------
    PermissionDef("approvals.view", "View approval requests and history"),
    PermissionDef("reports.view", "Run operational reports"),
    PermissionDef("reports.view_financial", "Run financial reports"),
    PermissionDef("reports.export", "Export reports to Excel or PDF"),
    PermissionDef("audit.view", "Read the audit log"),
    PermissionDef("notifications.view_own", "See your own notifications"),
    PermissionDef("notifications.broadcast", "Send a notification to other users"),
    PermissionDef("settings.view", "View system settings"),
    PermissionDef("settings.manage", "Change system settings", restricted=True),
    PermissionDef(
        "settings.manage_rules",
        "Create and edit business rules — tonnage limits, geofence radii, tolerances",
    ),
    PermissionDef(
        "settings.manage_workflows", "Create and edit approval workflows", restricted=True
    ),
    PermissionDef("attachments.view", "Download attachments"),
    PermissionDef("attachments.upload", "Upload attachments"),
    PermissionDef("attachments.delete", "Delete an attachment"),
)

ALL_PERMISSIONS: Final[tuple[PermissionDef, ...]] = P
PERMISSION_CODES: Final[frozenset[str]] = frozenset(p.code for p in P)
BY_CODE: Final[dict[str, PermissionDef]] = {p.code: p for p in P}
READ_ONLY_CODES: Final[frozenset[str]] = frozenset(p.code for p in P if p.is_read)
RESTRICTED_CODES: Final[frozenset[str]] = frozenset(p.code for p in P if p.restricted)

MODULES: Final[tuple[str, ...]] = tuple(dict.fromkeys(p.module for p in P))


def require_known(code: str) -> str:
    """Guard against typos in permission strings at import time."""
    if code not in PERMISSION_CODES:
        raise KeyError(f"Unknown permission '{code}'. Add it to the catalogue first.")
    return code

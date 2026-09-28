"""A land-development-appropriate chart of accounts, and the current fiscal
year's periods, ready for the first journal entry (docs/12 Q2).

These are starting defaults, not a prescription: an administrator is expected
to extend the chart and rename accounts to match how KRB actually reports.
What matters here is the *shape* — development cost as its own account type
rather than an ordinary expense, a GRN accrual account for the 3-way match
that arrives in a later slice — since that shape is harder to change once
journal entries have been posted against it.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import system_access_context
from app.core.types import utcnow
from app.modules.finance.domain.enums import AccountType, JournalSourceType
from app.modules.finance.models import Account, AccountingPeriod, PostingRule
from app.modules.finance.services import accounts as accounts_service
from app.modules.finance.services import periods as periods_service
from app.modules.finance.services import posting_rules as posting_rules_service
from app.modules.org.models import Company
from app.seeds.registry import SeedResult

# code, name, type, parent code (None for a root)
_ACCOUNTS: tuple[tuple[str, str, AccountType, str | None], ...] = (
    ("1000", "Assets", AccountType.ASSET, None),
    ("1100", "Cash and Bank", AccountType.ASSET, "1000"),
    ("1110", "Cash in Hand", AccountType.ASSET, "1100"),
    ("1120", "Bank Accounts", AccountType.ASSET, "1100"),
    ("1200", "Accounts Receivable", AccountType.ASSET, "1000"),
    ("1300", "Advances to Suppliers", AccountType.ASSET, "1000"),
    ("1400", "Inventory - Materials", AccountType.ASSET, "1000"),
    ("2000", "Liabilities", AccountType.LIABILITY, None),
    ("2100", "Accounts Payable", AccountType.LIABILITY, "2000"),
    # Goods received but not yet invoiced: the far side of a GRN posting, until
    # the vendor's invoice arrives and the 3-way match clears it into 2100.
    ("2110", "Goods Received Not Invoiced", AccountType.LIABILITY, "2000"),
    ("2200", "Withholding Tax Payable", AccountType.LIABILITY, "2000"),
    ("2300", "Sales Tax Payable", AccountType.LIABILITY, "2000"),
    ("3000", "Equity", AccountType.EQUITY, None),
    ("3100", "Share Capital", AccountType.EQUITY, "3000"),
    ("3200", "Retained Earnings", AccountType.EQUITY, "3000"),
    ("4000", "Revenue", AccountType.REVENUE, None),
    ("4100", "Sale of Developed Plots", AccountType.REVENUE, "4000"),
    ("4900", "Other Income", AccountType.REVENUE, "4000"),
    ("5000", "Expenses", AccountType.EXPENSE, None),
    ("5100", "Administrative Expenses", AccountType.EXPENSE, "5000"),
    ("5200", "Selling Expenses", AccountType.EXPENSE, "5000"),
    ("5900", "Other Expenses", AccountType.EXPENSE, "5000"),
    # Accumulates as work in progress on the land, not as a period expense —
    # see AccountType.COGS_DEV_COST.
    ("6000", "Development Cost", AccountType.COGS_DEV_COST, None),
    ("6100", "Materials Consumed", AccountType.COGS_DEV_COST, "6000"),
    ("6200", "Contractor and Labour Cost", AccountType.COGS_DEV_COST, "6000"),
    ("6300", "Site Overheads", AccountType.COGS_DEV_COST, "6000"),
)


async def seed_accounts(session: AsyncSession, company: Company) -> SeedResult:
    result = SeedResult("chart of accounts")
    ctx = system_access_context(company.id, UUID(int=0))
    by_code: dict[str, Account] = {}
    for code, name, account_type, parent_code in _ACCOUNTS:
        existing = await session.scalar(
            select(Account).where(Account.company_id == company.id, Account.code == code)
        )
        if existing is not None:
            by_code[code] = existing
            result.skipped += 1
            continue
        row = await accounts_service.create(
            session,
            ctx,
            accounts_service.AccountInput(
                code=code,
                name=name,
                account_type=account_type,
                parent_id=by_code[parent_code].id if parent_code else None,
            ),
        )
        row.created_by_id = None  # a seeded row has no human author
        by_code[code] = row
        result.created += 1
    return result


async def seed_periods(session: AsyncSession, company: Company) -> SeedResult:
    """The fiscal year containing today, and the next one, so there is always
    somewhere open to post into."""
    result = SeedResult("accounting periods")
    ctx = system_access_context(company.id, UUID(int=0))
    today = utcnow().date()
    current_fy = today.year if today.month >= company.fiscal_year_start_month else today.year - 1
    for fiscal_year in (current_fy, current_fy + 1):
        existing = (
            await session.scalar(
                select(func.count())
                .select_from(AccountingPeriod)
                .where(
                    AccountingPeriod.company_id == company.id,
                    AccountingPeriod.fiscal_year == fiscal_year,
                )
            )
        ) or 0
        rows = await periods_service.generate_fiscal_year(session, ctx, fiscal_year)
        result.created += len(rows) - existing
        result.skipped += existing
    return result


# (source_type, event, name, condition, debit code, credit code).
#
# GRN/RECEIPT: a stockable material's cost sits in inventory until it is
# issued; a non-stockable one (a contractor's work, say) is a development cost
# the moment it is received. A receipt with a purchase order behind it accrues
# (2110) until the vendor's invoice is matched (4c); one with none — a counter
# purchase, whose bill stands in for the invoice — is already payable (2100).
#
# INVENTORY/ISSUE: material leaving a store becomes what it left for — a
# development cost — straight away, at the store's average cost.
#
# INVENTORY/ADJUSTMENT: a correction is charged or credited to site overheads,
# not to inventory's own value — inventory only ever holds what a receipt or
# an issue put there or took out; an adjustment is the books catching up with
# what was actually on the shelf, and the difference is the site's, not the
# material's.
_POSTING_RULES: tuple[
    tuple[JournalSourceType, str, str, dict[str, object] | None, str, str], ...
] = (
    (
        JournalSourceType.GRN,
        "RECEIPT",
        "Stockable material, against a purchase order",
        {"and": [{"==": [{"var": "is_stockable"}, True]}, {"==": [{"var": "is_po_backed"}, True]}]},
        "1400",
        "2110",
    ),
    (
        JournalSourceType.GRN,
        "RECEIPT",
        "Stockable material, bought over the counter",
        {
            "and": [
                {"==": [{"var": "is_stockable"}, True]},
                {"==": [{"var": "is_po_backed"}, False]},
            ]
        },
        "1400",
        "2100",
    ),
    (
        JournalSourceType.GRN,
        "RECEIPT",
        "Non-stockable material, against a purchase order",
        {
            "and": [
                {"==": [{"var": "is_stockable"}, False]},
                {"==": [{"var": "is_po_backed"}, True]},
            ]
        },
        "6200",
        "2110",
    ),
    (
        JournalSourceType.GRN,
        "RECEIPT",
        "Non-stockable material, bought over the counter",
        {
            "and": [
                {"==": [{"var": "is_stockable"}, False]},
                {"==": [{"var": "is_po_backed"}, False]},
            ]
        },
        "6200",
        "2100",
    ),
    (
        JournalSourceType.INVENTORY,
        "ISSUE",
        "Material issued to a project",
        None,
        "6100",
        "1400",
    ),
    (
        JournalSourceType.INVENTORY,
        "ADJUSTMENT",
        "Count correction or write-off: more found",
        {"==": [{"var": "direction"}, "increase"]},
        "1400",
        "6300",
    ),
    (
        JournalSourceType.INVENTORY,
        "ADJUSTMENT",
        "Count correction or write-off: less found",
        {"==": [{"var": "direction"}, "decrease"]},
        "6300",
        "1400",
    ),
)


async def seed_posting_rules(session: AsyncSession, company: Company) -> SeedResult:
    result = SeedResult("posting rules")
    ctx = system_access_context(company.id, UUID(int=0))
    codes = {
        a.code: a.id
        for a in (
            await session.execute(
                select(Account).where(
                    Account.company_id == company.id,
                    Account.code.in_({c for *_, d, cr in _POSTING_RULES for c in (d, cr)}),
                )
            )
        )
        .scalars()
        .all()
    }
    for source_type, event, name, condition, debit_code, credit_code in _POSTING_RULES:
        existing = await session.scalar(
            select(func.count())
            .select_from(PostingRule)
            .where(
                PostingRule.company_id == company.id,
                PostingRule.source_type == source_type.value,
                PostingRule.event == event,
                PostingRule.name == name,
            )
        )
        if existing:
            result.skipped += 1
            continue
        row = await posting_rules_service.create(
            session,
            ctx,
            posting_rules_service.PostingRuleInput(
                source_type=source_type,
                event=event,
                name=name,
                condition=condition,
                debit_account_id=codes[debit_code],
                credit_account_id=codes[credit_code],
            ),
        )
        row.created_by_id = None
        result.created += 1
    return result

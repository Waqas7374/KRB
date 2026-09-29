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

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import system_access_context
from app.core.types import utcnow
from app.modules.finance.domain.enums import AccountType, JournalSourceType, TaxAppliesTo, TaxType
from app.modules.finance.models import Account, AccountingPeriod, BankAccount, PostingRule, TaxCode
from app.modules.finance.services import accounts as accounts_service
from app.modules.finance.services import bank_accounts as bank_accounts_service
from app.modules.finance.services import periods as periods_service
from app.modules.finance.services import posting_rules as posting_rules_service
from app.modules.finance.services import tax_codes as tax_codes_service
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
    # INVOICE/PAYABLE (4c): a matched invoice line clears its own debit
    # account (2110 for a 3-way match, nothing for a 2-way one — resolved per
    # line, not by this rule) into a real payable. Only credit_account_id is
    # ever read by vendor_invoices.approve(); debit_account_id is set to the
    # same account (2100) purely to satisfy the column's NOT NULL constraint
    # and is never itself posted to.
    (
        JournalSourceType.INVOICE,
        "PAYABLE",
        "Vendor invoice clears to Accounts Payable",
        None,
        "2100",
        "2100",
    ),
    # PAYMENT/EXECUTE (4d): a payment debits the same payable an invoice's own
    # approval credited (2100) and, if anything was retained, credits it to
    # Withholding Tax Payable (2200) — the debit and credit here are a real
    # pair this time, unlike INVOICE/PAYABLE's placeholder above. The third
    # leg (what actually left the bank) is the paying `bank_account`'s own
    # `gl_account_id`, resolved directly, never through this rule.
    (
        JournalSourceType.PAYMENT,
        "EXECUTE",
        "Payment clears Accounts Payable, withholds to Withholding Tax Payable",
        None,
        "2100",
        "2200",
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


# code, name, tax type, rate %, applies to, section code (withholding only).
# Illustrative Pakistani defaults (docs/12 Q1) — starting points, not a
# prescription: an administrator sets the actual rates that apply to KRB.
_TAX_CODES: tuple[tuple[str, str, TaxType, Decimal, TaxAppliesTo, str | None], ...] = (
    ("GST", "General Sales Tax", TaxType.SALES_TAX, Decimal("17.0000"), TaxAppliesTo.GOODS, None),
    (
        "SST",
        "Sales Tax on Services",
        TaxType.SALES_TAX,
        Decimal("15.0000"),
        TaxAppliesTo.SERVICES,
        None,
    ),
    (
        "WHT-GOODS",
        "Withholding Tax - Supply of Goods",
        TaxType.WITHHOLDING,
        Decimal("4.0000"),
        TaxAppliesTo.PAYMENT,
        "153(1)(a)",
    ),
    (
        "WHT-SERVICES",
        "Withholding Tax - Services",
        TaxType.WITHHOLDING,
        Decimal("8.0000"),
        TaxAppliesTo.PAYMENT,
        "153(1)(b)",
    ),
)


# title, account_no, bank_name, GL account code. The cash till is kept as a
# "bank account" row too, so a CASH payment credits a real account the same
# way every other method does, rather than a special case.
_BANK_ACCOUNTS: tuple[tuple[str, str, str, str], ...] = (
    ("Main Operating Account", "0001-0000001", "Sample Bank Ltd", "1120"),
    ("Cash Till", "CASH-01", "—", "1110"),
)


async def seed_bank_accounts(session: AsyncSession, company: Company) -> SeedResult:
    result = SeedResult("bank accounts")
    ctx = system_access_context(company.id, UUID(int=0))
    codes = {
        a.code: a.id
        for a in (
            await session.execute(
                select(Account).where(
                    Account.company_id == company.id,
                    Account.code.in_({c for *_, c in _BANK_ACCOUNTS}),
                )
            )
        )
        .scalars()
        .all()
    }
    for title, account_no, bank_name, gl_code in _BANK_ACCOUNTS:
        existing = await session.scalar(
            select(func.count())
            .select_from(BankAccount)
            .where(BankAccount.company_id == company.id, BankAccount.account_no == account_no)
        )
        if existing:
            result.skipped += 1
            continue
        row = await bank_accounts_service.create(
            session,
            ctx,
            bank_accounts_service.BankAccountInput(
                account_title=title,
                account_no=account_no,
                bank_name=bank_name,
                gl_account_id=codes[gl_code],
            ),
        )
        row.created_by_id = None
        result.created += 1
    return result


async def seed_tax_codes(session: AsyncSession, company: Company) -> SeedResult:
    result = SeedResult("tax codes")
    ctx = system_access_context(company.id, UUID(int=0))
    for code, name, tax_type, rate_pct, applies_to, section_code in _TAX_CODES:
        existing = await session.scalar(
            select(TaxCode).where(TaxCode.company_id == company.id, TaxCode.code == code)
        )
        if existing is not None:
            result.skipped += 1
            continue
        row = await tax_codes_service.create(
            session,
            ctx,
            tax_codes_service.TaxCodeInput(
                code=code,
                name=name,
                tax_type=tax_type,
                rate_pct=rate_pct,
                applies_to=applies_to,
                section_code=section_code,
            ),
        )
        row.created_by_id = None
        result.created += 1
    return result

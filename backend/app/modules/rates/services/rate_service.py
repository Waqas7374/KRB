"""Propose, approve and supersede vendor rates.

The rule that shapes everything here (docs/05 §4): **a rate is never edited.**
A change is a new period. It is proposed, routed through the approval engine
(small changes can be configured to approve themselves), and only when it is
approved does it come into force — at which point the period it replaces is
closed the day before, in the same transaction, and a history row records the
old value, the new one, who and why.

Periods in one scope only move forward: a new rate may not start on or before
the start of an existing one. That keeps every date resolving to at most one
rate per scope, which the database also guarantees with an exclusion
constraint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import (
    BusinessRuleError,
    PermissionDeniedError,
    ValidationError,
    VersionConflictError,
)
from app.core.pagination import PageParams, apply_sort
from app.core.scoping import assert_in_scope
from app.core.types import utcnow
from app.modules.approvals.services import engine as approvals
from app.modules.approvals.services import registry
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.services.writer import record as record_audit
from app.modules.masterdata.services import material_lookup
from app.modules.org.services import document_lookup
from app.modules.rates.domain import resolution
from app.modules.rates.domain.enums import RateSource, RateStatus
from app.modules.rates.models import VendorRate, VendorRateHistory
from app.modules.vendors.services import vendor_lookup

DOC_TYPE = "vendor_rate"
PERM_VIEW = "rates.view"
PERM_CREATE = "rates.create"
PERM_APPROVE = "rates.approve"
PERM_HISTORY = "rates.view_history"

_BARRED_VENDORS = {"BLACKLISTED", "INACTIVE"}


def repository(session: AsyncSession) -> ScopedRepository[VendorRate]:
    return ScopedRepository(
        session,
        VendorRate,
        entity_name="Vendor rate",
        sortable={"rate", "effective_from", "effective_to", "status", "created_at", "updated_at"},
        searchable=(),
        default_sort="-effective_from",
    )


@dataclass(frozen=True, slots=True)
class RateInput:
    vendor_id: UUID
    material_id: UUID
    unit_id: UUID
    rate: Decimal
    currency_code: str
    project_id: UUID | None
    site_id: UUID | None
    effective_from: date
    reason: str | None
    source: str = RateSource.MANUAL.value


def _fail(field: str, message: str) -> ValidationError:
    return ValidationError(
        message, errors=[{"field": field, "code": "invalid", "message": message}]
    )


def view(row: VendorRate) -> resolution.RateView:
    return resolution.RateView(
        id=row.id,
        vendor_id=row.vendor_id,
        material_id=row.material_id,
        unit_id=row.unit_id,
        rate=row.rate,
        currency_code=row.currency_code,
        project_id=row.project_id,
        site_id=row.site_id,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        source=row.source,
    )


def _same_scope(row: VendorRate, data_project: UUID | None, data_site: UUID | None) -> bool:
    return row.project_id == data_project and row.site_id == data_site


async def _scope_rows(
    session: AsyncSession, company_id: UUID, data: RateInput, *, statuses: tuple[str, ...]
) -> list[VendorRate]:
    rows = await session.execute(
        select(VendorRate).where(
            VendorRate.company_id == company_id,
            VendorRate.vendor_id == data.vendor_id,
            VendorRate.material_id == data.material_id,
            VendorRate.unit_id == data.unit_id,
            VendorRate.status.in_(statuses),
        )
    )
    return [r for r in rows.scalars() if _same_scope(r, data.project_id, data.site_id)]


def _latest_active(rows: list[VendorRate]) -> VendorRate | None:
    return max(rows, key=lambda r: r.effective_from, default=None)


# -----------------------------------------------------------------------------
# Propose
# -----------------------------------------------------------------------------


async def propose(session: AsyncSession, ctx: AccessContext, data: RateInput) -> VendorRate:
    if data.rate < 0:
        raise _fail("rate", "must not be negative")
    vendor = (
        await vendor_lookup.vendors(session, company_id=ctx.company_id, vendor_ids={data.vendor_id})
    ).get(data.vendor_id)
    if vendor is None:
        raise _fail("vendor_id", "unknown vendor")
    if vendor.status in _BARRED_VENDORS:
        raise BusinessRuleError(
            "vendor_not_priceable",
            f"{vendor.name} is {vendor.status.lower()}; a rate cannot be set for it.",
        )
    materials = await material_lookup.materials(
        session, company_id=ctx.company_id, material_ids={data.material_id}
    )
    material = materials.get(data.material_id)
    if material is None or not material.is_active:
        raise _fail("material_id", "unknown or deactivated material")
    if data.unit_id not in material.unit_ids:
        raise _fail("unit_id", f"not a unit configured for {material.sku}")

    project_id = data.project_id
    if data.site_id is not None or project_id is not None:
        try:
            place = await document_lookup.resolve_place(
                session,
                company_id=ctx.company_id,
                project_id=project_id,
                site_id=data.site_id,
            )
        except document_lookup.PlaceMismatchError as exc:
            raise ValidationError(
                str(exc), errors=[{"field": exc.field, "code": "invalid", "message": str(exc)}]
            ) from exc
        project_id = place.project_id or place.site_project_id
    scoped = RateInput(
        data.vendor_id,
        data.material_id,
        data.unit_id,
        data.rate,
        data.currency_code,
        project_id,
        data.site_id,
        data.effective_from,
        data.reason,
        data.source,
    )
    assert_in_scope(
        ctx,
        PERM_CREATE,
        company_id=ctx.company_id,
        project_id=project_id,
        site_id=data.site_id,
        entity="Vendor rate",
    )

    if await _scope_rows(
        session, ctx.company_id, scoped, statuses=(RateStatus.PENDING_APPROVAL.value,)
    ):
        raise BusinessRuleError(
            "rate_change_pending",
            "A rate change for this vendor, material and scope is already awaiting approval. "
            "Decide or withdraw it first.",
        )
    existing = await _scope_rows(
        session, ctx.company_id, scoped, statuses=(RateStatus.ACTIVE.value,)
    )
    latest = _latest_active(existing)
    if latest is not None and data.effective_from <= latest.effective_from:
        raise BusinessRuleError(
            "rate_period_not_forward",
            f"An earlier or equal period already starts on {latest.effective_from:%d %b %Y}. "
            "A new rate must start after it: rates only move forward.",
        )
    previous = (
        latest if latest is not None and latest.effective_from < data.effective_from else None
    )
    if previous is not None and previous.rate == data.rate:
        raise BusinessRuleError(
            "rate_unchanged", f"That is already the rate in force ({previous.rate:f})."
        )

    row = VendorRate(
        company_id=ctx.company_id,
        vendor_id=data.vendor_id,
        material_id=data.material_id,
        unit_id=data.unit_id,
        rate=data.rate,
        currency_code=data.currency_code,
        project_id=project_id,
        site_id=data.site_id,
        effective_from=data.effective_from,
        status=RateStatus.PENDING_APPROVAL.value,
        source=data.source,
        reason=data.reason,
        previous_rate=previous.rate if previous else None,
        change_pct=resolution.change_pct(previous.rate if previous else None, data.rate),
        supersedes_id=previous.id if previous else None,
        requested_by_id=ctx.user_id,
        submitted_at=utcnow(),
        created_by_id=ctx.user_id,
    )
    session.add(row)
    await session.flush()

    place = await document_lookup.resolve_place(
        session, company_id=ctx.company_id, project_id=project_id, site_id=data.site_id
    )
    subject = approvals.ApprovalSubject(
        doc_type=DOC_TYPE,
        doc_id=row.id,
        company_id=ctx.company_id,
        initiated_by=ctx.user_id,
        context=_approval_context(row, vendor, material, place, ctx),
        document_hash=_hash(row),
        doc_number=f"{vendor.code} / {material.sku}",
        summary=_summary(row, vendor.name, material.name),
        amount=None,
        currency_code=row.currency_code,
        # A rate has no page of its own; the generic approval page shows the
        # change, the trail and the decision buttons.
        link_path=f"/approvals/document/{DOC_TYPE}/{row.id}",
        project_id=row.project_id,
        site_id=row.site_id,
    )
    # One savepoint: a routing refusal must not leave a pending rate behind.
    async with session.begin_nested():
        approval = await approvals.submit(session, subject)
        row.approval_request_id = approval.id
        await session.flush()
    return row


def _summary(row: VendorRate, vendor: str, material: str) -> str:
    move = (
        f"{row.previous_rate:f} -> {row.rate:f} ({row.change_pct:+.2f}%)"
        if row.previous_rate is not None and row.change_pct is not None
        else f"new rate {row.rate:f}"
    )
    return f"{vendor} / {material}: {move} from {row.effective_from:%d %b %Y}"


# -----------------------------------------------------------------------------
# Approval integration
# -----------------------------------------------------------------------------

CONTEXT_VARIABLES = frozenset(
    {
        "new_rate",
        "previous_rate",
        "change_pct",
        "is_first_rate",
        "is_increase",
        "vendor.*",
        "material.*",
        "project.*",
        "site.*",
        "requester.*",
    }
)


def _approval_context(
    row: VendorRate,
    vendor: vendor_lookup.VendorInfo,
    material: material_lookup.MaterialInfo,
    place: document_lookup.DocumentPlace,
    ctx: AccessContext,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "new_rate": row.rate,
        "is_first_rate": row.previous_rate is None,
        "vendor": {"id": vendor.id, "code": vendor.code, "name": vendor.name},
        "material": {"id": material.id, "sku": material.sku, "name": material.name},
        "project": {"id": place.project_id, "code": place.project_code},
        "site": {"id": place.site_id, "code": place.site_code},
        "requester": {"id": ctx.user_id, "roles": sorted(ctx.role_codes)},
    }
    if row.previous_rate is not None:
        context["previous_rate"] = row.previous_rate
        context["is_increase"] = row.rate > row.previous_rate
    if row.change_pct is not None:
        context["change_pct"] = row.change_pct
    return context


def _hash(row: VendorRate) -> str:
    return approvals.document_hash(
        {
            "vendor_id": row.vendor_id,
            "material_id": row.material_id,
            "unit_id": row.unit_id,
            "rate": row.rate,
            "currency": row.currency_code,
            "project_id": row.project_id,
            "site_id": row.site_id,
            "effective_from": row.effective_from,
            "previous_rate": row.previous_rate,
        }
    )


async def _apply(session: AsyncSession, row: VendorRate) -> None:
    """Bring an approved rate into force: close the period it replaces, put it
    in the record, and note the change in history and the audit log."""
    same = await session.execute(
        select(VendorRate).where(
            VendorRate.company_id == row.company_id,
            VendorRate.vendor_id == row.vendor_id,
            VendorRate.material_id == row.material_id,
            VendorRate.unit_id == row.unit_id,
            VendorRate.status == RateStatus.ACTIVE.value,
            VendorRate.id != row.id,
        )
    )
    scope_rows = [r for r in same.scalars() if _same_scope(r, row.project_id, row.site_id)]
    latest = _latest_active(scope_rows)
    previous: VendorRate | None = None
    if latest is not None:
        if latest.effective_from >= row.effective_from:
            # Someone else's rate was approved into this scope meanwhile.
            raise BusinessRuleError(
                "rate_period_not_forward",
                f"A rate starting {latest.effective_from:%d %b %Y} was approved after this "
                "change was submitted. Withdraw it and submit again from a later date.",
            )
        previous = latest
        limit = row.effective_from - timedelta(days=1)
        if previous.effective_to is None or previous.effective_to > limit:
            previous.effective_to = limit
            previous.version += 1
    row.status = RateStatus.ACTIVE.value
    row.approved_at = utcnow()
    row.supersedes_id = previous.id if previous else None
    row.previous_rate = previous.rate if previous else None
    row.change_pct = resolution.change_pct(row.previous_rate, row.rate)
    row.version += 1
    await session.flush()
    session.add(
        VendorRateHistory(
            company_id=row.company_id,
            vendor_rate_id=row.id,
            vendor_id=row.vendor_id,
            material_id=row.material_id,
            superseded_rate_id=previous.id if previous else None,
            old_rate=previous.rate if previous else None,
            new_rate=row.rate,
            change_pct=row.change_pct,
            effective_from=row.effective_from,
            reason=row.reason,
            changed_by_id=row.requested_by_id,
            approval_request_id=row.approval_request_id,
            changed_at=utcnow(),
        )
    )
    label = f"{row.previous_rate:f} -> {row.rate:f}" if previous else f"{row.rate:f} (first rate)"
    await record_audit(
        session,
        action=AuditAction.APPROVE,
        entity_type="VendorRate",
        entity_id=row.id,
        entity_label=str(row.id)[:8],
        project_id=row.project_id,
        site_id=row.site_id,
        summary=f"Vendor rate {label} effective {row.effective_from:%d %b %Y}"
        + (f": {row.reason}" if row.reason else ""),
    )
    await session.flush()


class VendorRateApprovals:
    spec = registry.DocumentTypeSpec(
        doc_type=DOC_TYPE,
        label="Vendor rate change",
        approve_permission=PERM_APPROVE,
        context_variables=CONTEXT_VARIABLES,
    )

    async def _load(self, session: AsyncSession, doc_id: UUID) -> VendorRate:
        return (
            await session.execute(select(VendorRate).where(VendorRate.id == doc_id))
        ).scalar_one()

    async def current_hash(self, session: AsyncSession, doc_id: UUID) -> str | None:
        return _hash(await self._load(session, doc_id))

    async def pending_document_ids(self, session: AsyncSession) -> dict[UUID, UUID]:
        rows = await session.execute(
            select(VendorRate.id, VendorRate.company_id).where(
                VendorRate.status == RateStatus.PENDING_APPROVAL.value
            )
        )
        return dict(rows.tuples().all())

    async def on_approved(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        row = await self._load(session, outcome.doc_id)
        await _apply(session, row)

    async def _end(
        self, session: AsyncSession, outcome: registry.ApprovalOutcome, status: RateStatus
    ) -> None:
        row = await self._load(session, outcome.doc_id)
        row.status = status.value
        row.decision_reason = outcome.reason
        row.version += 1

    async def on_rejected(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        await self._end(session, outcome, RateStatus.REJECTED)

    async def on_changes_requested(
        self, session: AsyncSession, outcome: registry.ApprovalOutcome
    ) -> None:
        # A rate is not edited: "changes" means withdraw it and propose again.
        await self._end(session, outcome, RateStatus.WITHDRAWN)

    async def on_recalled(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        await self._end(session, outcome, RateStatus.WITHDRAWN)


registry.register(VendorRateApprovals())


# -----------------------------------------------------------------------------
# Queries
# -----------------------------------------------------------------------------


def _apply_current(stmt: Select[Any], today: date) -> Select[Any]:
    return stmt.where(
        VendorRate.status == RateStatus.ACTIVE.value,
        VendorRate.effective_from <= today,
        (VendorRate.effective_to.is_(None)) | (VendorRate.effective_to >= today),
    )


async def list_rates(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    filters: dict[str, Any],
    current_only: bool,
) -> tuple[list[VendorRate], int]:
    repo = repository(session)
    stmt = repo.base_query(ctx, PERM_VIEW)
    stmt = repo.apply_filters(stmt, filters)
    if current_only:
        stmt = _apply_current(stmt, utcnow().date())
    total = (
        await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ) or 0
    stmt = apply_sort(stmt, VendorRate, page.sort, allowed=repo.sortable, default=repo.default_sort)
    rows = (
        (await session.execute(stmt.limit(page.limit).offset(page.offset))).scalars().unique().all()
    )
    return list(rows), total


RateKey = tuple[UUID, UUID, UUID, UUID | None, UUID | None]
TREND_POINTS = 12


def key_of(row: VendorRate) -> RateKey:
    return (row.vendor_id, row.material_id, row.unit_id, row.project_id, row.site_id)


async def trends(
    session: AsyncSession, ctx: AccessContext, rows: list[VendorRate]
) -> tuple[dict[RateKey, list[VendorRate]], set[RateKey]]:
    """For a page of rates: each one's periods, oldest first (the last `TREND_POINTS`), and
    which of them have a change waiting for approval.

    Only rows the caller may see are read, so a sparkline never carries a rate from a scope
    they cannot open.
    """
    if not rows:
        return {}, set()
    repo = repository(session)
    stmt = repo.base_query(ctx, PERM_VIEW).where(
        VendorRate.vendor_id.in_({r.vendor_id for r in rows}),
        VendorRate.material_id.in_({r.material_id for r in rows}),
        VendorRate.status.in_((RateStatus.ACTIVE.value, RateStatus.PENDING_APPROVAL.value)),
    )
    wanted = {key_of(r) for r in rows}
    periods: dict[RateKey, list[VendorRate]] = {}
    pending: set[RateKey] = set()
    for row in (await session.execute(stmt)).scalars().unique().all():
        key = key_of(row)
        if key not in wanted:
            continue
        if row.status == RateStatus.PENDING_APPROVAL.value:
            pending.add(key)
        else:
            periods.setdefault(key, []).append(row)
    return (
        {k: sorted(v, key=lambda r: r.effective_from)[-TREND_POINTS:] for k, v in periods.items()},
        pending,
    )


async def get(session: AsyncSession, ctx: AccessContext, rate_id: UUID) -> VendorRate:
    return await repository(session).get(ctx, PERM_VIEW, rate_id)


async def update_notes(
    session: AsyncSession,
    ctx: AccessContext,
    rate_id: UUID,
    notes: str | None,
    *,
    expected_version: int | None,
) -> VendorRate:
    """Notes are the one editable field: correcting a note rewrites nothing that
    money depends on."""
    row = await repository(session).get_for_update(ctx, PERM_CREATE, rate_id)
    if expected_version is not None and row.version != expected_version:
        raise VersionConflictError(
            f"This rate was changed by someone else (version {row.version}, "
            f"you had {expected_version}). Reload and try again."
        )
    row.notes = notes
    row.updated_by_id = ctx.user_id
    row.version += 1
    await session.flush()
    return row


async def history(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    vendor_id: UUID | None,
    material_id: UUID | None,
    page: PageParams,
) -> tuple[list[VendorRateHistory], int]:
    if not ctx.has(PERM_HISTORY):
        raise PermissionDeniedError(PERM_HISTORY)
    # History spans every scope, so it needs the permission company-wide: a
    # site-scoped grant must not reveal what other sites pay.
    if not ctx.scope_for(PERM_HISTORY).is_company_wide:
        raise PermissionDeniedError(PERM_HISTORY)
    stmt = select(VendorRateHistory).where(VendorRateHistory.company_id == ctx.company_id)
    if vendor_id:
        stmt = stmt.where(VendorRateHistory.vendor_id == vendor_id)
    if material_id:
        stmt = stmt.where(VendorRateHistory.material_id == material_id)
    total = (
        await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ) or 0
    rows = (
        (
            await session.execute(
                stmt.order_by(VendorRateHistory.changed_at.desc())
                .limit(page.limit)
                .offset(page.offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total

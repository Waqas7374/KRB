"""Manual journal entries: a person's own adjustment to the ledger, as opposed
to one a subledger event raised for itself (docs/02 §8).

A manual entry is the one kind of posting that is not the automatic
consequence of an already-approved document, so it is the one kind that goes
through the approval engine itself. Approval and posting are the same moment:
there is no "approved but not yet posted" status in the model, so the handler
below posts the instant the last step signs off — exactly like a stock
adjustment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import BusinessRuleError, PermissionDeniedError, VersionConflictError
from app.core.pagination import PageParams
from app.core.types import utcnow
from app.modules.approvals.services import engine as approvals
from app.modules.approvals.services import registry
from app.modules.finance.domain.enums import JournalStatus
from app.modules.finance.models import JournalEntry
from app.modules.finance.services import ledger

DOC_TYPE = "journal_entry"
PERM_VIEW = "finance.gl.view"
PERM_CREATE = "finance.gl.create"
PERM_POST = "finance.gl.post"
PERM_REVERSE = "finance.gl.reverse"

CONTEXT_VARIABLES = frozenset({"amount_abs", "source_type", "line_count", "requester.*"})


def repository(session: AsyncSession) -> ScopedRepository[JournalEntry]:
    return ScopedRepository(
        session,
        JournalEntry,
        entity_name="Journal entry",
        sortable={"je_number", "entry_date", "status", "created_at", "updated_at"},
        searchable=("je_number", "description", "reference"),
        default_sort="-entry_date",
    )


@dataclass(frozen=True, slots=True)
class JournalEntryInput:
    entry_date: date
    description: str
    reference: str | None
    lines: list[ledger.LineInput]


async def _get_for_update(session: AsyncSession, ctx: AccessContext, je_id: UUID) -> JournalEntry:
    return await repository(session).get_for_update(ctx, PERM_CREATE, je_id)


def _check_version(entry: JournalEntry, expected: int | None) -> None:
    if expected is not None and entry.version != expected:
        raise VersionConflictError(
            f"{entry.je_number} was changed by someone else (version {entry.version}, "
            f"you had {expected}). Reload and try again."
        )


async def create(
    session: AsyncSession, ctx: AccessContext, data: JournalEntryInput
) -> JournalEntry:
    return await ledger.build_draft(
        session,
        ctx,
        entry_date=data.entry_date,
        description=data.description,
        reference=data.reference,
        lines=data.lines,
    )


async def update(
    session: AsyncSession,
    ctx: AccessContext,
    je_id: UUID,
    data: JournalEntryInput,
    *,
    expected_version: int | None,
) -> JournalEntry:
    entry = await _get_for_update(session, ctx, je_id)
    _check_version(entry, expected_version)
    if not JournalStatus(entry.status).is_editable:
        raise BusinessRuleError(
            "journal_entry_not_editable",
            f"{entry.je_number} is {entry.status.lower().replace('_', ' ')}; only a draft can be "
            "edited.",
        )
    # See the comment in `submit`: the locking fetch above loads with
    # `noload("*")`, so `entry.lines` must be loaded for real before
    # `replace_draft_lines` clears it — otherwise the old lines are never
    # deleted and the new ones collide with them on `line_no`.
    await session.refresh(entry, attribute_names=["lines"])
    entry.entry_date = data.entry_date
    entry.description = data.description
    entry.reference = data.reference
    await ledger.replace_draft_lines(session, ctx, entry, data.lines)
    entry.decision_reason = None
    entry.version += 1
    entry.updated_by_id = ctx.user_id
    await session.flush()
    return entry


async def delete_draft(session: AsyncSession, ctx: AccessContext, je_id: UUID) -> None:
    """A draft that never happened. Nothing has been posted, so there is
    nothing for the ledger's append-only rule to protect — it is simply removed."""
    entry = await _get_for_update(session, ctx, je_id)
    if entry.status != JournalStatus.DRAFT.value:
        raise BusinessRuleError(
            "journal_entry_not_draft", f"{entry.je_number} is not a draft; it cannot be deleted."
        )
    await session.delete(entry)
    await session.flush()


def _hash(entry: JournalEntry) -> str:
    return approvals.document_hash(
        {
            "entry_date": entry.entry_date,
            "description": entry.description,
            "lines": [
                {
                    "account_id": line.account_id,
                    "debit": line.debit,
                    "credit": line.credit,
                    "project_id": line.project_id,
                    "site_id": line.site_id,
                }
                for line in entry.lines
            ],
        }
    )


async def submit(
    session: AsyncSession, ctx: AccessContext, je_id: UUID, *, expected_version: int | None
) -> JournalEntry:
    entry = await _get_for_update(session, ctx, je_id)
    _check_version(entry, expected_version)
    if not JournalStatus(entry.status).is_editable:
        raise BusinessRuleError(
            "journal_entry_not_submittable",
            f"{entry.je_number} is {entry.status.lower().replace('_', ' ')}; only a draft can be "
            "submitted.",
        )
    # `_get_for_update`'s locking query loads with `noload("*")`; if `lines`
    # had already been expired (e.g. by an earlier commit in this session) it
    # would resolve to an empty collection rather than lazy-loading, and the
    # hash below would not match the one `current_hash` recomputes at decide
    # time. Load it for real before it feeds the approval's document hash.
    await session.refresh(entry, attribute_names=["lines"])
    subject = approvals.ApprovalSubject(
        doc_type=DOC_TYPE,
        doc_id=entry.id,
        company_id=entry.company_id,
        initiated_by=ctx.user_id,
        context={
            "amount_abs": entry.total_debit,
            "source_type": entry.source_type,
            "line_count": len(entry.lines),
            "requester": {"id": ctx.user_id, "roles": sorted(ctx.role_codes)},
        },
        document_hash=_hash(entry),
        doc_number=entry.je_number,
        summary=entry.description,
        amount=entry.total_debit,
        currency_code="PKR",
        link_path=f"/finance/journal-entries/{entry.id}",
    )
    async with session.begin_nested():
        entry.status = JournalStatus.PENDING_APPROVAL.value
        entry.submitted_at = utcnow()
        entry.decision_reason = None
        entry.version += 1
        entry.updated_by_id = ctx.user_id
        await session.flush()
        request = await approvals.submit(session, subject)
        entry.approval_request_id = request.id
        await session.flush()
    return entry


async def withdraw(
    session: AsyncSession, ctx: AccessContext, je_id: UUID, reason: str | None
) -> JournalEntry:
    entry = await _get_for_update(session, ctx, je_id)
    if entry.status != JournalStatus.PENDING_APPROVAL.value or entry.approval_request_id is None:
        raise BusinessRuleError(
            "journal_entry_not_pending",
            f"{entry.je_number} is not pending approval.",
        )
    await approvals.recall(session, ctx, entry.approval_request_id, reason)
    await session.refresh(entry)
    return entry


async def reverse(
    session: AsyncSession, ctx: AccessContext, je_id: UUID, reason: str
) -> JournalEntry:
    if not ctx.has(PERM_REVERSE):
        raise PermissionDeniedError(PERM_REVERSE)
    entry = await repository(session).get(ctx, PERM_VIEW, je_id)
    if entry.status != JournalStatus.POSTED.value:
        raise BusinessRuleError(
            "journal_entry_not_posted",
            f"{entry.je_number} is not posted; there is nothing to reverse.",
        )
    if len((reason or "").strip()) < 5:
        raise BusinessRuleError("reason_required", "Say why this entry is being reversed.")
    await session.refresh(entry, attribute_names=["lines"])
    return await ledger.reverse(session, ctx, entry, reason=reason.strip())


class JournalEntryApprovals:
    spec = registry.DocumentTypeSpec(
        doc_type=DOC_TYPE,
        label="Journal entry",
        approve_permission=PERM_POST,
        context_variables=CONTEXT_VARIABLES,
    )

    async def _load(self, session: AsyncSession, doc_id: UUID) -> JournalEntry:
        row = (
            await session.execute(select(JournalEntry).where(JournalEntry.id == doc_id))
        ).scalar_one()
        await session.refresh(row, attribute_names=["lines"])
        return row

    async def current_hash(self, session: AsyncSession, doc_id: UUID) -> str | None:
        return _hash(await self._load(session, doc_id))

    async def pending_document_ids(self, session: AsyncSession) -> dict[UUID, UUID]:
        rows = await session.execute(
            select(JournalEntry.id, JournalEntry.company_id).where(
                JournalEntry.status == JournalStatus.PENDING_APPROVAL.value
            )
        )
        return dict(rows.tuples().all())

    async def on_approved(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        entry = await self._load(session, outcome.doc_id)
        # A system-level context: the manual entry was already approved by a
        # human; ledger.post only re-checks the period and flips the status.
        from app.core.access import system_access_context

        sys_ctx = system_access_context(
            entry.company_id, outcome.actor_user_id or entry.created_by_id or UUID(int=0)
        )
        await ledger.post(session, sys_ctx, entry, actor_id=outcome.actor_user_id)
        entry.decision_reason = outcome.reason

    async def _back_to_draft(
        self, session: AsyncSession, outcome: registry.ApprovalOutcome
    ) -> None:
        entry = await self._load(session, outcome.doc_id)
        entry.status = JournalStatus.DRAFT.value
        entry.decision_reason = outcome.reason
        entry.version += 1
        await session.flush()

    async def on_rejected(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        await self._back_to_draft(session, outcome)

    async def on_changes_requested(
        self, session: AsyncSession, outcome: registry.ApprovalOutcome
    ) -> None:
        await self._back_to_draft(session, outcome)

    async def on_recalled(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        await self._back_to_draft(session, outcome)


registry.register(JournalEntryApprovals())


async def list_entries(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[JournalEntry], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, je_id: UUID) -> JournalEntry:
    entry = await repository(session).get(ctx, PERM_VIEW, je_id)
    await session.refresh(entry, attribute_names=["lines"])
    return entry

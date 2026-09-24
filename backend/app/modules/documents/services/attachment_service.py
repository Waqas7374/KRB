"""Attachment lifecycle: presign, confirm, list, download, remove.

Three steps, matching §28 and §35 ("validate uploads by content, never trust
the client"):

1. **presign** — the caller declares what it wants to upload; a row is
   created in an unconfirmed state and the client gets a direct, short-lived
   upload URL. The request never carries the file's bytes.
2. **confirm** — after the client's own PUT succeeds, it tells us so. We
   `HEAD` the object ourselves to get the *real* size and content type from
   storage, not the client's claim, and only then mark the row uploaded.
3. **remove** — soft, with a reason. Attachments on transactional records are
   evidence; the object is not deleted from storage, only hidden and later
   swept by a retention job (not built in v1).

An unconfirmed row older than an hour (the client never called back, or the
upload failed) is swept by a maintenance job — see `platform/numbering.py`'s
sibling pattern for how that kind of housekeeping is scheduled.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.config import settings
from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.core.logging import get_logger
from app.core.types import utcnow, uuid7
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.services.writer import record as record_audit
from app.modules.documents.domain.enums import EntityType, ScanStatus
from app.modules.documents.models import Attachment
from app.platform import outbox
from app.platform.storage import (
    DEFAULT_ALLOWED_TYPES,
    PresignedUpload,
    get_object_store,
)

log = get_logger("attachments")

# Which permission gates uploading to which kind of entity. A vendor document
# and a purchase-order attachment are different privileges even though both
# flow through this one table.
_UPLOAD_PERMISSIONS: dict[EntityType, str] = {
    EntityType.VENDOR: "vendors.update",
    EntityType.EMPLOYEE: "hr.employee.update",
    EntityType.PROJECT: "projects.update",
    EntityType.SITE: "sites.update",
    EntityType.PURCHASE_REQUEST: "procurement.pr.create",
    EntityType.RFQ: "procurement.rfq.create",
    EntityType.QUOTATION: "procurement.quotation.record",
    EntityType.PURCHASE_ORDER: "procurement.po.create",
    EntityType.DELIVERY: "deliveries.create",
    EntityType.GRN: "grn.create",
    EntityType.VENDOR_INVOICE: "finance.ap.create",
    EntityType.PAYMENT: "finance.payment.request",
    EntityType.JOURNAL_ENTRY: "finance.gl.create",
    EntityType.BUDGET: "finance.budget.create",
    EntityType.STOCK_ADJUSTMENT: "inventory.adjust",
    EntityType.LEAVE_REQUEST: "hr.leave.request",
}


def _upload_permission(entity_type: EntityType) -> str:
    return _UPLOAD_PERMISSIONS.get(entity_type, "attachments.upload")


async def presign_upload(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    entity_type: EntityType,
    entity_id: UUID,
    file_name: str,
    content_type: str,
    size_bytes: int,
    document_type: str | None,
    description: str | None,
) -> tuple[Attachment, PresignedUpload]:
    required = _upload_permission(entity_type)
    if not ctx.has(required) and not ctx.has("attachments.upload"):
        raise PermissionDeniedError(required)

    if content_type not in DEFAULT_ALLOWED_TYPES:
        raise BusinessRuleError(
            "unsupported_content_type",
            f"'{content_type}' is not an accepted file type.",
        )

    if size_bytes <= 0 or size_bytes > settings.max_upload_bytes:
        raise BusinessRuleError(
            "file_too_large",
            f"File must be between 1 byte and {settings.max_upload_bytes:,} bytes.",
        )

    store = get_object_store()
    key = store.build_key(
        company_id=str(ctx.company_id),
        entity_type=entity_type.value,
        entity_id=str(entity_id),
        filename=file_name,
    )

    attachment = Attachment(
        id=uuid7(),
        company_id=ctx.company_id,
        entity_type=entity_type.value,
        entity_id=entity_id,
        document_type=document_type,
        file_name=file_name,
        content_type=content_type,
        size_bytes=size_bytes,
        storage_key=key,
        scan_status=ScanStatus.SKIPPED.value,
        description=description,
    )
    session.add(attachment)
    await session.flush()

    upload = store.presign_upload(key, content_type=content_type)
    log.info(
        "attachment.presigned",
        attachment_id=str(attachment.id),
        entity_type=entity_type.value,
        entity_id=str(entity_id),
    )
    return attachment, upload


async def confirm_upload(
    session: AsyncSession, ctx: AccessContext, *, attachment_id: UUID
) -> Attachment:
    """Verify the object actually landed in storage and record its real
    size/type — never the client's claim."""
    attachment = await _get(session, ctx, attachment_id)
    if attachment.is_confirmed:
        return attachment

    store = get_object_store()
    head = store.head(attachment.storage_key)
    if head is None:
        raise BusinessRuleError(
            "upload_not_found",
            "No object was found at the presigned location. The upload may have failed or expired.",
        )

    actual_size = head.get("size_bytes")
    if isinstance(actual_size, int) and actual_size > 0:
        attachment.size_bytes = actual_size
    actual_type = head.get("content_type")
    if isinstance(actual_type, str) and actual_type:
        attachment.content_type = actual_type
    # checksum_sha256 is left null pending a future virus/hash scan job.
    attachment.uploaded_at = utcnow()

    await outbox.emit(
        session,
        outbox.DomainEvent(
            event_type="attachment.uploaded",
            aggregate_type="Attachment",
            aggregate_id=attachment.id,
            payload={
                "entity_type": attachment.entity_type,
                "entity_id": str(attachment.entity_id),
                "file_name": attachment.file_name,
            },
            company_id=ctx.company_id,
        ),
    )
    return attachment


async def list_for_entity(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    entity_type: EntityType,
    entity_id: UUID,
) -> list[Attachment]:
    if not ctx.has("attachments.view"):
        raise PermissionDeniedError("attachments.view")
    rows = (
        await session.execute(
            select(Attachment)
            .where(
                Attachment.company_id == ctx.company_id,
                Attachment.entity_type == entity_type.value,
                Attachment.entity_id == entity_id,
                Attachment.uploaded_at.is_not(None),
                Attachment.removed_at.is_(None),
            )
            .order_by(Attachment.created_at.desc())
        )
    ).scalars()
    return list(rows.all())


async def get_download_url(
    session: AsyncSession, ctx: AccessContext, *, attachment_id: UUID
) -> tuple[Attachment, str]:
    attachment = await _get(session, ctx, attachment_id)
    if not ctx.has("attachments.view"):
        raise PermissionDeniedError("attachments.view")
    if not attachment.is_confirmed or attachment.is_removed:
        raise NotFoundError("Attachment", attachment_id)

    store = get_object_store()
    presigned = store.presign_download(attachment.storage_key, filename=attachment.file_name)
    return attachment, presigned.url


async def remove(
    session: AsyncSession, ctx: AccessContext, *, attachment_id: UUID, reason: str
) -> Attachment:
    if not ctx.has("attachments.delete"):
        raise PermissionDeniedError("attachments.delete")

    attachment = await _get(session, ctx, attachment_id)
    if attachment.is_removed:
        raise BusinessRuleError("already_removed", "This attachment was already removed.")

    attachment.removed_at = utcnow()
    attachment.removed_by_id = ctx.user_id
    attachment.removal_reason = reason

    await record_audit(
        session,
        action=AuditAction.SOFT_DELETE,
        entity_type="Attachment",
        entity_id=attachment.id,
        entity_label=attachment.file_name,
        company_id=ctx.company_id,
        summary=f"Attachment removed: {attachment.file_name} ({reason})",
    )
    return attachment


async def _get(session: AsyncSession, ctx: AccessContext, attachment_id: UUID) -> Attachment:
    attachment = (
        await session.execute(
            select(Attachment).where(
                Attachment.id == attachment_id, Attachment.company_id == ctx.company_id
            )
        )
    ).scalar_one_or_none()
    if attachment is None:
        raise NotFoundError("Attachment", attachment_id)
    return attachment

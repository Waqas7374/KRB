"""Attachments.

One polymorphic table for every entity that can carry documents (§28):
vendors, employees, purchase requests, RFQs, quotations, POs, GRNs, invoices,
payments, deliveries.

`storage_key` is opaque, so R2, S3, B2 and MinIO are interchangeable without
touching application logic. Nothing in the schema knows which provider holds
the bytes.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constraints import enum_check
from app.core.db import CompanyModel
from app.modules.documents.domain.enums import ScanStatus


class Attachment(CompanyModel):
    __tablename__ = "attachments"
    __audited__ = True

    # --- What it is attached to ---------------------------------------------
    # Not a foreign key: the target is one of ~15 tables. Referential integrity
    # is enforced by the service that creates the row, which already has the
    # parent loaded and permission-checked.
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    # Optional classification within an entity: 'challan', 'invoice_copy',
    # 'cnic', 'signature', 'truck_photo'.
    document_type: Mapped[str | None] = mapped_column(String(60))

    # --- The file ------------------------------------------------------------
    file_name: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))

    # --- Lifecycle -----------------------------------------------------------
    # Rows are created before the upload completes, then confirmed. An
    # unconfirmed row older than an hour is swept up by a maintenance job.
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ScanStatus.SKIPPED.value
    )
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Expiry tracking for documents that lapse (CNIC, licences, certificates).
    expires_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    description: Mapped[str | None] = mapped_column(String(500))
    # Attachments on transactional records are evidence: never hard-deleted,
    # only marked removed with a reason.
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    removal_reason: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_attachments_storage_key"),
        enum_check("scan_status", ScanStatus),
        CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        Index("ix_attachments_entity", "entity_type", "entity_id"),
        Index(
            "ix_attachments_pending_upload",
            "created_at",
            postgresql_where="uploaded_at IS NULL",
        ),
        Index("ix_attachments_expiring", "company_id", "expires_on"),
    )

    @property
    def is_confirmed(self) -> bool:
        return self.uploaded_at is not None

    @property
    def is_removed(self) -> bool:
        return self.removed_at is not None


__all__ = ["Attachment"]

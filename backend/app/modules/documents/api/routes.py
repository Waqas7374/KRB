"""Attachment endpoints.

One polymorphic surface for every entity that can carry documents (§28):
`entity_type` + `entity_id` say what the file is attached to, and the
permission required to attach one depends on which entity that is — see
`attachment_service._UPLOAD_PERMISSIONS`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import Access, SessionDep, UowDep
from app.modules.documents.domain.enums import EntityType
from app.modules.documents.schemas import (
    AttachmentRead,
    AttachmentRemove,
    DownloadUrlResponse,
    PresignUploadRequest,
    PresignUploadResponse,
)
from app.modules.documents.services import attachment_service

router = APIRouter()


@router.post(
    "/presign",
    response_model=PresignUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a direct upload URL — the file never passes through the API",
)
async def presign_upload(
    payload: PresignUploadRequest, ctx: Access, uow: UowDep
) -> PresignUploadResponse:
    attachment, upload = await attachment_service.presign_upload(
        uow.session,
        ctx,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        file_name=payload.file_name,
        content_type=payload.content_type,
        size_bytes=payload.size_bytes,
        document_type=payload.document_type,
        description=payload.description,
    )
    return PresignUploadResponse(
        attachment_id=attachment.id,
        storage_key=upload.storage_key,
        upload_url=upload.upload_url,
        method=upload.method,
        expires_in=upload.expires_in,
    )


@router.post(
    "/{attachment_id}/confirm",
    response_model=AttachmentRead,
    summary="Confirm an upload completed — verified against storage, not the client's claim",
)
async def confirm_upload(attachment_id: UUID, ctx: Access, uow: UowDep) -> AttachmentRead:
    attachment = await attachment_service.confirm_upload(
        uow.session, ctx, attachment_id=attachment_id
    )
    return AttachmentRead.model_validate(attachment)


@router.get("", response_model=list[AttachmentRead], summary="Attachments on one entity")
async def list_attachments(
    ctx: Access,
    session: SessionDep,
    entity_type: Annotated[EntityType, Query()],
    entity_id: Annotated[UUID, Query()],
) -> list[AttachmentRead]:
    rows = await attachment_service.list_for_entity(
        session, ctx, entity_type=entity_type, entity_id=entity_id
    )
    return [AttachmentRead.model_validate(row) for row in rows]


@router.get(
    "/{attachment_id}/download",
    response_model=DownloadUrlResponse,
    summary="A short-lived direct download URL",
)
async def get_download_url(
    attachment_id: UUID, ctx: Access, session: SessionDep
) -> DownloadUrlResponse:
    attachment, url = await attachment_service.get_download_url(
        session, ctx, attachment_id=attachment_id
    )
    return DownloadUrlResponse(
        attachment_id=attachment.id,
        file_name=attachment.file_name,
        url=url,
        expires_in=900,
    )


@router.post(
    "/{attachment_id}/remove",
    response_model=AttachmentRead,
    summary="Soft-remove an attachment — the object stays in storage as evidence",
)
async def remove_attachment(
    attachment_id: UUID, payload: AttachmentRemove, ctx: Access, uow: UowDep
) -> AttachmentRead:
    attachment = await attachment_service.remove(
        uow.session, ctx, attachment_id=attachment_id, reason=payload.reason
    )
    return AttachmentRead.model_validate(attachment)

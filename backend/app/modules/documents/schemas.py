"""Attachment request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.documents.domain.enums import EntityType


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PresignUploadRequest(ApiModel):
    entity_type: EntityType
    entity_id: UUID
    file_name: Annotated[str, Field(min_length=1, max_length=300)]
    content_type: Annotated[str, Field(min_length=3, max_length=160)]
    size_bytes: Annotated[int, Field(gt=0, le=26_214_400)]
    document_type: Annotated[str | None, Field(max_length=60)] = None
    description: Annotated[str | None, Field(max_length=500)] = None


class PresignUploadResponse(ApiModel):
    attachment_id: UUID
    storage_key: str
    upload_url: str
    method: str
    expires_in: int


class AttachmentRead(ApiModel):
    id: UUID
    entity_type: str
    entity_id: UUID
    document_type: str | None
    file_name: str
    content_type: str
    size_bytes: int
    is_confirmed: bool
    uploaded_at: datetime | None
    expires_on: datetime | None
    description: str | None
    created_at: datetime


class DownloadUrlResponse(ApiModel):
    attachment_id: UUID
    file_name: str
    url: str
    expires_in: int


class AttachmentRemove(ApiModel):
    reason: Annotated[str, Field(min_length=3, max_length=300)]

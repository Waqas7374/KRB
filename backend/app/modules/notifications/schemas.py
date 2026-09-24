"""Notification request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.notifications.domain.enums import NotificationChannel


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class NotificationRead(ApiModel):
    id: UUID
    notification_type: str
    priority: str
    title: str
    body: str
    link_path: str | None
    entity_type: str | None
    entity_id: UUID | None
    data: dict[str, Any]
    read_at: datetime | None
    created_at: datetime


class NotificationInbox(ApiModel):
    items: list[NotificationRead]
    total: int
    unread_count: int


class PreferenceRead(ApiModel):
    notification_type: str
    channel: str
    is_enabled: bool


class PreferenceUpdate(ApiModel):
    notification_type: Annotated[str, Field(min_length=2, max_length=60)]
    channel: NotificationChannel
    is_enabled: bool

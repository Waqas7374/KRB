"""Notifications.

Channel-agnostic by design (§26): v1 delivers in-app and email, and adding push
or SMS later is an adapter plus a row in `notification_preferences`, not a
schema change. `user_devices.push_token` is already captured.

Notifications are produced by outbox handlers, never inline in a request, so a
failed email cannot roll back an approved purchase order.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constraints import enum_check
from app.core.db import BaseModel, CompanyModel
from app.modules.notifications.domain.enums import (
    DeliveryStatus,
    NotificationChannel,
    NotificationPriority,
)


class Notification(CompanyModel):
    """One message to one user on one channel.

    A single event that must reach a user by two channels produces two rows, so
    "the email failed but they saw it in the app" is representable.
    """

    __tablename__ = "notifications"

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    notification_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(
        String(20), nullable=False, default=NotificationChannel.IN_APP.value
    )
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default=NotificationPriority.NORMAL.value
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(String(2000), nullable=False)
    # Where clicking it should land, e.g. /deliveries/018f-...
    link_path: Mapped[str | None] = mapped_column(String(300))

    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # --- Delivery ------------------------------------------------------------
    delivery_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DeliveryStatus.PENDING.value
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when the user acts on it, so a resolved alert stops nagging.
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        enum_check("channel", NotificationChannel),
        enum_check("priority", NotificationPriority),
        enum_check("delivery_status", DeliveryStatus),
        # The notification bell: unread, newest first.
        Index(
            "ix_notifications_user_unread",
            "user_id",
            "created_at",
            postgresql_where="read_at IS NULL",
        ),
        Index("ix_notifications_entity", "entity_type", "entity_id"),
    )

    @property
    def is_read(self) -> bool:
        return self.read_at is not None


class NotificationPreference(BaseModel):
    """Per-user opt-out, per type, per channel.

    Absence of a row means "use the default for this type", so preferences do
    not have to be back-filled when a new notification type ships.
    """

    __tablename__ = "notification_preferences"

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notification_type: Mapped[str] = mapped_column(String(60), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "notification_type",
            "channel",
            name="uq_notification_preferences_user_type_channel",
        ),
        enum_check("channel", NotificationChannel),
    )


__all__ = ["Notification", "NotificationPreference"]

"""Notification creation and delivery.

Channel-agnostic (§26): `send()` writes the in-app row always, and additionally
queues an email if the user has not disabled that channel for this
notification type. Push and SMS are unimplemented adapters — the channel
column and the preference row already accommodate them, so turning one on
later is a deployment change, not a schema change.

Notifications are produced from the outbox drain, never inline in a request
(see `app.workers.tasks.outbox`), so a failed send can never roll back the
business transaction that caused it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.types import utcnow, uuid7
from app.modules.notifications.domain.enums import (
    DeliveryStatus,
    NotificationChannel,
    NotificationPriority,
)
from app.modules.notifications.models import Notification, NotificationPreference

log = get_logger("notifications")


async def is_channel_enabled(
    session: AsyncSession, *, user_id: UUID, notification_type: str, channel: NotificationChannel
) -> bool:
    """Absence of a preference row means "use the default" — enabled — so a
    new notification type never needs a migration to back-fill preferences."""
    pref = (
        await session.execute(
            select(NotificationPreference.is_enabled).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.notification_type == notification_type,
                NotificationPreference.channel == channel.value,
            )
        )
    ).scalar_one_or_none()
    return True if pref is None else bool(pref)


async def send(
    session: AsyncSession,
    *,
    company_id: UUID,
    user_id: UUID,
    notification_type: str,
    title: str,
    body: str,
    priority: NotificationPriority = NotificationPriority.NORMAL,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    link_path: str | None = None,
    data: dict[str, Any] | None = None,
) -> list[Notification]:
    """Create one row per enabled channel for this user and type.

    Returns every row created (usually one, IN_APP; two if email is also
    enabled), so a caller — in practice only the outbox drain — can log what
    actually went out.
    """
    created: list[Notification] = []

    for channel in (NotificationChannel.IN_APP, NotificationChannel.EMAIL):
        if not await is_channel_enabled(
            session, user_id=user_id, notification_type=notification_type, channel=channel
        ):
            continue

        notification = Notification(
            id=uuid7(),
            company_id=company_id,
            user_id=user_id,
            notification_type=notification_type,
            channel=channel.value,
            priority=priority.value,
            title=title[:200],
            body=body[:2000],
            link_path=link_path,
            entity_type=entity_type,
            entity_id=entity_id,
            data=data or {},
            delivery_status=DeliveryStatus.PENDING.value,
        )
        session.add(notification)
        created.append(notification)

    if created:
        await session.flush()
    return created


async def mark_sent(session: AsyncSession, notification_id: UUID) -> None:
    notification = (
        await session.execute(select(Notification).where(Notification.id == notification_id))
    ).scalar_one()
    notification.delivery_status = DeliveryStatus.SENT.value
    notification.sent_at = utcnow()


async def mark_failed(session: AsyncSession, notification_id: UUID, reason: str) -> None:
    notification = (
        await session.execute(select(Notification).where(Notification.id == notification_id))
    ).scalar_one()
    notification.delivery_status = DeliveryStatus.FAILED.value
    notification.failed_at = utcnow()
    notification.failure_reason = reason[:500]
    notification.attempts += 1


# -----------------------------------------------------------------------------
# Inbox
# -----------------------------------------------------------------------------


async def list_inbox(
    session: AsyncSession,
    *,
    user_id: UUID,
    unread_only: bool,
    limit: int,
    offset: int,
) -> tuple[list[Notification], int]:
    stmt = select(Notification).where(
        Notification.user_id == user_id, Notification.channel == NotificationChannel.IN_APP.value
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))

    total = await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = (
        await session.execute(
            stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars()
    return list(rows.all()), total or 0


async def unread_count(session: AsyncSession, *, user_id: UUID) -> int:
    return (
        await session.scalar(
            select(func.count()).where(
                Notification.user_id == user_id,
                Notification.channel == NotificationChannel.IN_APP.value,
                Notification.read_at.is_(None),
            )
        )
    ) or 0


async def mark_read(session: AsyncSession, *, user_id: UUID, notification_id: UUID) -> Notification:
    notification = (
        await session.execute(
            select(Notification).where(
                Notification.id == notification_id, Notification.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if notification is None:
        raise NotFoundError("Notification", notification_id)
    if notification.read_at is None:
        notification.read_at = utcnow()
    return notification


async def mark_all_read(session: AsyncSession, *, user_id: UUID) -> int:
    result: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.channel == NotificationChannel.IN_APP.value,
            Notification.read_at.is_(None),
        )
        .values(read_at=utcnow())
    )
    return int(result.rowcount or 0)


# -----------------------------------------------------------------------------
# Preferences
# -----------------------------------------------------------------------------


async def get_preferences(session: AsyncSession, *, user_id: UUID) -> list[NotificationPreference]:
    rows = await session.execute(
        select(NotificationPreference).where(NotificationPreference.user_id == user_id)
    )
    return list(rows.scalars().all())


async def set_preference(
    session: AsyncSession,
    *,
    user_id: UUID,
    notification_type: str,
    channel: NotificationChannel,
    is_enabled: bool,
) -> NotificationPreference:
    existing = (
        await session.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.notification_type == notification_type,
                NotificationPreference.channel == channel.value,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.is_enabled = is_enabled
        return existing

    pref = NotificationPreference(
        id=uuid7(),
        user_id=user_id,
        notification_type=notification_type,
        channel=channel.value,
        is_enabled=is_enabled,
    )
    session.add(pref)
    await session.flush()
    return pref

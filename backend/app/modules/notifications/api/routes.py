"""Notification endpoints: the in-app inbox and channel preferences.

Every route here is scoped to the caller's own notifications — there is no
"view someone else's inbox" concept, so these are gated by
`notifications.view_own` rather than a scoped permission, and the query
always filters by `user.id` regardless of what a caller might otherwise be
permitted to see.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, SessionDep, UowDep, require
from app.modules.notifications.schemas import (
    NotificationInbox,
    NotificationRead,
    PreferenceRead,
    PreferenceUpdate,
)
from app.modules.notifications.services import notification_service

router = APIRouter(dependencies=[require("notifications.view_own")])


@router.get("", response_model=NotificationInbox, summary="Your in-app inbox")
async def list_inbox(
    user: CurrentUser,
    session: SessionDep,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NotificationInbox:
    items, total = await notification_service.list_inbox(
        session, user_id=user.id, unread_only=unread_only, limit=limit, offset=offset
    )
    unread = await notification_service.unread_count(session, user_id=user.id)
    return NotificationInbox(
        items=[NotificationRead.model_validate(i) for i in items],
        total=total,
        unread_count=unread,
    )


@router.post(
    "/{notification_id}/read",
    response_model=NotificationRead,
    summary="Mark one notification read",
)
async def mark_read(notification_id: UUID, user: CurrentUser, uow: UowDep) -> NotificationRead:
    notification = await notification_service.mark_read(
        uow.session, user_id=user.id, notification_id=notification_id
    )
    return NotificationRead.model_validate(notification)


@router.post(
    "/read-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark every notification read",
)
async def mark_all_read(user: CurrentUser, uow: UowDep) -> None:
    await notification_service.mark_all_read(uow.session, user_id=user.id)


@router.get("/preferences", response_model=list[PreferenceRead], summary="Your channel preferences")
async def get_preferences(user: CurrentUser, session: SessionDep) -> list[PreferenceRead]:
    rows = await notification_service.get_preferences(session, user_id=user.id)
    return [PreferenceRead.model_validate(r) for r in rows]


@router.put(
    "/preferences",
    response_model=PreferenceRead,
    summary="Turn a notification type on or off for one channel",
)
async def set_preference(
    payload: PreferenceUpdate, user: CurrentUser, uow: UowDep
) -> PreferenceRead:
    pref = await notification_service.set_preference(
        uow.session,
        user_id=user.id,
        notification_type=payload.notification_type,
        channel=payload.channel,
        is_enabled=payload.is_enabled,
    )
    return PreferenceRead.model_validate(pref)

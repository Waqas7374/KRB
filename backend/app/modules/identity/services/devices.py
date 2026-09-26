"""The device registry as the sync API uses it (docs/06 §4).

The registry itself belongs to identity: login registers a phone, head office
can revoke it. Sync only *reports in* — a phone that pushes or pulls is seen,
and a revoked one is refused before it can read or write anything.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.core.types import utcnow
from app.modules.identity.domain.enums import DevicePlatform
from app.modules.identity.models import UserDevice

REVOKED = "This device's access has been revoked. Contact head office."


async def check_in(
    session: AsyncSession,
    *,
    user_id: UUID,
    device_uid: str,
    platform: str | None = None,
    app_version: str | None = None,
    push_token: str | None = None,
    create: bool,
) -> UserDevice | None:
    """Record that `device_uid` reached the server.

    A push registers a phone we have not seen (`create=True`); a pull only
    updates one we know. A revoked device is refused in both cases.
    """
    device = (
        await session.execute(
            select(UserDevice).where(
                UserDevice.user_id == user_id, UserDevice.device_uid == device_uid
            )
        )
    ).scalar_one_or_none()
    now = utcnow()
    if device is None:
        if not create:
            return None
        device = UserDevice(
            user_id=user_id,
            device_uid=device_uid,
            platform=platform or DevicePlatform.ANDROID.value,
            first_seen_at=now,
            created_by_id=user_id,
        )
        session.add(device)
    if device.revoked_at is not None:
        raise PermissionDeniedError(detail=REVOKED)
    device.app_version = app_version or device.app_version
    device.push_token = push_token or device.push_token
    device.last_seen_at = now
    device.last_sync_at = now
    await session.flush()
    return device

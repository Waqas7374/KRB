"""FastAPI dependencies.

Lives in the interface layer, which is the only layer allowed to know about
both `core` and the modules.

The three-tier check from docs/03-rbac.md §5 starts here:

1. `CurrentUser`      — authenticated, not locked, not deactivated
2. `require("...")`   — holds the permission
3. scope filtering    — applied inside repositories, not here
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request, params
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.cache import get_redis
from app.core.context import update_context
from app.core.db import UnitOfWork, get_session, get_uow
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.pagination import PageParams, page_params
from app.core.security import decode_access_token
from app.core.types import utcnow
from app.modules.access.services.resolver import resolve_cached
from app.modules.identity.domain.enums import UserStatus
from app.modules.identity.models import User, UserSession

# auto_error=False so a missing header produces our own problem document
# rather than Starlette's bare 403.
_bearer = HTTPBearer(auto_error=False, description="Access token from /auth/login")

SessionDep = Annotated[AsyncSession, Depends(get_session)]
UowDep = Annotated[UnitOfWork, Depends(get_uow)]
PageDep = Annotated[PageParams, Depends(page_params)]


async def get_current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    """Authenticate the request and load the acting user.

    Checks the session has not been revoked, which is what makes "log out
    everywhere" and "revoke this device" take effect immediately despite the
    access token still being cryptographically valid.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Provide a bearer access token")

    payload = decode_access_token(credentials.credentials)

    user_id = UUID(payload["sub"])
    session_id = UUID(payload["sid"]) if payload.get("sid") else None

    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise AuthenticationError("Account no longer exists")

    if user.status in {UserStatus.DEACTIVATED.value, UserStatus.SUSPENDED.value}:
        raise AuthenticationError(f"Account is {user.status.lower()}")

    if user.locked_until is not None and user.locked_until > utcnow():
        raise AuthenticationError("Account is temporarily locked")

    if session_id is not None:
        user_session = (
            await session.execute(select(UserSession).where(UserSession.id == session_id))
        ).scalar_one_or_none()
        if user_session is None or user_session.revoked_at is not None:
            raise AuthenticationError("Session has been revoked; sign in again")

    # Make the actor available to the audit writer and the logger.
    update_context(user_id=user.id, company_id=user.company_id)
    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_access_context(user: CurrentUser, session: SessionDep) -> AccessContext:
    """Resolve the caller's permissions and scopes.

    Cached in Redis against the user's `permissions_version`, so a grant change
    invalidates it immediately rather than after a TTL.
    """
    ctx = await resolve_cached(
        session,
        user_id=user.id,
        company_id=user.company_id,
        permissions_version=user.permissions_version,
        cache=get_redis(),
    )
    return ctx


Access = Annotated[AccessContext, Depends(get_access_context)]


def require(*permissions: str) -> params.Depends:
    """Dependency asserting the caller holds **all** the named permissions.

    Route-level, coarse check. Scope is enforced separately, in the repository
    query, because a permission without a scope predicate would leak rows from
    other projects.

        @router.post("/purchase-orders/{po_id}/approve",
                     dependencies=[Depends(require("procurement.po.approve"))])
    """

    async def _dependency(ctx: Access) -> AccessContext:
        for permission in permissions:
            if not ctx.has(permission):
                raise PermissionDeniedError(permission)
        return ctx

    dependency: params.Depends = Depends(_dependency)
    return dependency


def require_any(*permissions: str) -> params.Depends:
    """Dependency asserting the caller holds at least one of the permissions."""

    async def _dependency(ctx: Access) -> AccessContext:
        if not ctx.has_any(*permissions):
            raise PermissionDeniedError(detail="Requires one of: " + ", ".join(sorted(permissions)))
        return ctx

    dependency: params.Depends = Depends(_dependency)
    return dependency


async def block_read_only_writes(request: Request, ctx: Access) -> None:
    """Refuse any mutating request from a user whose every role is read-only.

    The Auditor role already holds no write permissions; this is the second,
    independent guard, so a misconfigured role grant cannot turn an auditor
    into an editor.
    """
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if ctx.is_read_only:
        raise PermissionDeniedError(
            detail="This account has read-only access and cannot modify data"
        )

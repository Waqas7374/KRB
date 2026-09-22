"""Authentication endpoints.

`/auth/password/forgot` always answers 202 with the same message whether or not
the account exists — anything else turns the endpoint into an account
enumerator.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from app.api.deps import Access, CurrentUser, SessionDep, UowDep
from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.security import decode_access_token
from app.core.types import utcnow
from app.modules.access.services import grants as grant_service
from app.modules.identity.models import UserDevice, UserSession
from app.modules.identity.schemas import (
    ChangePasswordRequest,
    CompanySummary,
    DeviceSummary,
    ForgotPasswordRequest,
    LoginRequest,
    MeResponse,
    MessageResponse,
    RefreshRequest,
    ResetPasswordRequest,
    RoleGrantSummary,
    ScopeSummary,
    SessionSummary,
    TokenResponse,
    UserProfile,
)
from app.modules.identity.services import auth as auth_service
from app.modules.org.services import company_service

log = get_logger("api.auth")

router = APIRouter()


def _token_response(pair: auth_service.TokenPair) -> TokenResponse:
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=settings.access_token_ttl_minutes * 60,
        refresh_expires_at=pair.refresh_expires_at,
        user=UserProfile.model_validate(pair.user),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Sign in with an email or a phone number",
)
async def login(payload: LoginRequest, uow: UowDep) -> TokenResponse:
    device = (
        auth_service.DeviceInfo(
            device_uid=payload.device.device_uid,
            platform=payload.device.platform.value,
            model=payload.device.model,
            os_version=payload.device.os_version,
            app_version=payload.device.app_version,
            push_token=payload.device.push_token,
            is_rooted=payload.device.is_rooted,
        )
        if payload.device
        else None
    )

    pair = await auth_service.login(
        uow.session,
        identifier=payload.identifier,
        password=payload.password,
        device=device,
    )
    return _token_response(pair)


@router.post("/refresh", response_model=TokenResponse, summary="Rotate the refresh token")
async def refresh(payload: RefreshRequest, uow: UowDep) -> TokenResponse:
    pair = await auth_service.refresh(uow.session, refresh_token=payload.refresh_token)
    return _token_response(pair)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Sign out of this session",
)
async def logout(
    request: Request,
    user: CurrentUser,
    uow: UowDep,
) -> MessageResponse:
    # The session id is a claim on the presented token, not a body parameter,
    # so a caller cannot sign someone else out.
    authorization = request.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ").strip()
    session_id = UUID(decode_access_token(token)["sid"])

    await auth_service.logout(uow.session, session_id=session_id, user_id=user.id)
    return MessageResponse(message="Signed out")


@router.get("/me", response_model=MeResponse, summary="The signed-in user and their access")
async def me(user: CurrentUser, ctx: Access, session: SessionDep) -> MeResponse:
    # Both of these come from another module's service, not its ORM models:
    # module boundaries are enforced by tests/unit/test_architecture.py.
    company = await company_service.get_profile(session, user.company_id)
    grants = await grant_service.list_active_grants(session, user.id)

    return MeResponse(
        user=UserProfile.model_validate(user),
        company=CompanySummary.model_validate(company),
        roles=[
            RoleGrantSummary(
                role_code=grant.role_code,
                role_name=grant.role_name,
                scope_type=grant.scope_type,
                scope_id=grant.scope_id,
            )
            for grant in grants
        ],
        permissions=sorted(ctx.permissions),
        is_superuser=ctx.is_superuser,
        is_read_only=ctx.is_read_only,
        scopes={
            code: ScopeSummary(
                is_global=scope.is_global,
                is_company_wide=scope.is_company_wide,
                project_ids=sorted(scope.project_ids),
                site_ids=sorted(scope.site_ids),
                department_ids=sorted(scope.department_ids),
            )
            for code, scope in ctx.scopes.items()
        },
    )


@router.post(
    "/password/change",
    response_model=MessageResponse,
    summary="Change your own password",
)
async def change_password(
    payload: ChangePasswordRequest, user: CurrentUser, uow: UowDep
) -> MessageResponse:
    await auth_service.change_password(
        uow.session,
        user=user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return MessageResponse(
        message="Password changed",
        detail="You have been signed out of all other devices.",
    )


@router.post(
    "/password/forgot",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request a password-reset link",
)
async def forgot_password(payload: ForgotPasswordRequest, uow: UowDep) -> MessageResponse:
    user, _kind = await auth_service.find_user_by_identifier(uow.session, payload.identifier)

    if user is not None and user.can_log_in:
        token = await auth_service.create_password_reset(uow.session, user=user)
        # Phase 1 delivers this through the notification outbox; until the
        # email template lands it is logged at debug level only in
        # development, never returned in the response.
        log.info("auth.password_reset_requested", user_id=str(user.id))
        if not settings.is_production:
            log.debug("auth.password_reset_token", token=token)

    # Identical response either way.
    return MessageResponse(
        message="If that account exists, a reset link has been sent.",
        detail="The link is valid for "
        f"{settings.password_reset_ttl_minutes} minutes and can be used once.",
    )


@router.post(
    "/password/reset",
    response_model=MessageResponse,
    summary="Complete a password reset",
)
async def reset_password(payload: ResetPasswordRequest, uow: UowDep) -> MessageResponse:
    await auth_service.complete_password_reset(
        uow.session, token=payload.token, new_password=payload.new_password
    )
    return MessageResponse(message="Password has been reset. Sign in with your new password.")


@router.get(
    "/sessions",
    response_model=list[SessionSummary],
    summary="Your active sessions",
)
async def list_sessions(
    request: Request, user: CurrentUser, session: SessionDep
) -> list[SessionSummary]:
    authorization = request.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ").strip()
    current_session_id = UUID(decode_access_token(token)["sid"])

    rows = (
        (
            await session.execute(
                select(UserSession)
                .where(
                    UserSession.user_id == user.id,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > utcnow(),
                )
                .order_by(UserSession.issued_at.desc())
            )
        )
        .scalars()
        .all()
    )

    return [
        SessionSummary(
            id=row.id,
            ip=str(row.ip) if row.ip else None,
            user_agent=row.user_agent,
            device_id=row.device_id,
            issued_at=row.issued_at,
            expires_at=row.expires_at,
            last_used_at=row.last_used_at,
            is_current=row.id == current_session_id,
        )
        for row in rows
    ]


@router.delete(
    "/sessions/{session_id}",
    response_model=MessageResponse,
    summary="Sign out one session",
)
async def revoke_session(session_id: UUID, user: CurrentUser, uow: UowDep) -> MessageResponse:
    await auth_service.logout(uow.session, session_id=session_id, user_id=user.id)
    return MessageResponse(message="Session signed out")


@router.get(
    "/devices",
    response_model=list[DeviceSummary],
    summary="Your registered mobile devices",
)
async def list_devices(user: CurrentUser, session: SessionDep) -> list[DeviceSummary]:
    rows = (
        (
            await session.execute(
                select(UserDevice)
                .where(UserDevice.user_id == user.id)
                .order_by(UserDevice.last_seen_at.desc().nullslast())
            )
        )
        .scalars()
        .all()
    )
    return [DeviceSummary.model_validate(row) for row in rows]


@router.post(
    "/devices/{device_id}/revoke",
    response_model=MessageResponse,
    summary="Revoke a device",
)
async def revoke_device(device_id: UUID, user: CurrentUser, uow: UowDep) -> MessageResponse:
    device = (
        await uow.session.execute(
            select(UserDevice).where(UserDevice.id == device_id, UserDevice.user_id == user.id)
        )
    ).scalar_one_or_none()
    if device is None:
        raise NotFoundError("Device", device_id)

    device.revoked_at = utcnow()
    device.is_trusted = False
    return MessageResponse(
        message="Device revoked",
        detail="Its queued deliveries stay on the device and can still be recovered by "
        "signing in again after the device is re-approved.",
    )

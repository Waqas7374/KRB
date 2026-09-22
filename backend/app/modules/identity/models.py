"""Identity ORM models: users, sessions, devices, password resets.

A user logs in with **email on the web and phone on mobile** (§13) — both
resolve to the same row, and both are unique per company.

Refresh tokens are opaque and stored only as an HMAC, in a rotation chain:
presenting a token that has already been rotated means it leaked, and the whole
chain is revoked. Access tokens are short-lived JWTs that carry no permissions,
so revoking a role takes effect on the next request rather than in 15 minutes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constraints import enum_check
from app.core.db import BaseModel, CompanyModel, VersionMixin
from app.modules.identity.domain.enums import DevicePlatform, UserStatus


class User(CompanyModel, VersionMixin):
    __tablename__ = "users"
    __audited__ = True
    __audit_exclude__ = ("password_hash", "permissions_version")

    # --- Identification ------------------------------------------------------
    email: Mapped[str | None] = mapped_column(CITEXT)
    phone: Mapped[str | None] = mapped_column(String(32))
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(80))

    # --- Credentials ---------------------------------------------------------
    password_hash: Mapped[str | None] = mapped_column(String(255))
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Lockout -------------------------------------------------------------
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_ip: Mapped[str | None] = mapped_column(INET)

    # --- State ---------------------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=UserStatus.INVITED.value
    )

    # --- Defaults that shape the UI and the mobile app -----------------------
    # use_alter: users <-> projects and users <-> sites are genuine FK cycles
    # (a project has a manager, a user has a default project). Postgres needs
    # these added as ALTER statements after both tables exist.
    default_project_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL", use_alter=True),
    )
    default_site_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="SET NULL", use_alter=True),
    )
    # FK added in Phase 5 with the employees table.
    employee_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))

    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en-PK")
    timezone: Mapped[str | None] = mapped_column(String(64))

    # Bumped whenever this user's grants change. The access token carries the
    # value it was minted with, so the server can tell a stale token from a
    # current one and invalidate the cached AccessContext without a cache sweep.
    permissions_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        # Case-insensitive uniqueness comes free from CITEXT for email.
        UniqueConstraint("company_id", "email", name="uq_users_company_id_email"),
        UniqueConstraint("company_id", "phone", name="uq_users_company_id_phone"),
        enum_check("status", UserStatus),
        # Someone must be reachable by at least one identifier, because both
        # are login paths.
        CheckConstraint("email IS NOT NULL OR phone IS NOT NULL", name="email_or_phone_required"),
        CheckConstraint("failed_login_count >= 0", name="failed_login_count_non_negative"),
        Index("ix_users_company_id_status", "company_id", "status"),
        Index(
            "ix_users_full_name_trgm",
            "full_name",
            postgresql_using="gin",
            postgresql_ops={"full_name": "gin_trgm_ops"},
        ),
    )

    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", lazy="noload", cascade="all, delete-orphan"
    )

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE.value

    @property
    def can_log_in(self) -> bool:
        return self.status in {UserStatus.ACTIVE.value, UserStatus.PASSWORD_RESET_REQUIRED.value}


class UserSession(BaseModel):
    """One refresh-token lifetime.

    `rotated_from_id` forms a chain. Presenting a refresh token whose row is
    already rotated or revoked is treated as theft: the entire chain is revoked
    and the user must log in again.
    """

    __tablename__ = "user_sessions"

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    device_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_devices.id", ondelete="SET NULL")
    )
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(400))

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(80))
    rotated_from_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_sessions.id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint("refresh_token_hash", name="uq_user_sessions_refresh_token_hash"),
        CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
        Index(
            "ix_user_sessions_user_id_active",
            "user_id",
            "expires_at",
            postgresql_where="revoked_at IS NULL",
        ),
    )

    user: Mapped[User] = relationship(back_populates="sessions", lazy="noload")

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None


class UserDevice(BaseModel):
    """A mobile device registry.

    Also the owner of the mobile sync cursor, and the unit head office revokes
    when a phone is lost. `push_token` is captured from v1 so push
    notifications need only a channel adapter later.
    """

    __tablename__ = "user_devices"

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Client-generated, stable across app restarts and reinstalls where possible.
    device_uid: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DevicePlatform.ANDROID.value
    )
    model: Mapped[str | None] = mapped_column(String(120))
    os_version: Mapped[str | None] = mapped_column(String(40))
    app_version: Mapped[str | None] = mapped_column(String(40))

    push_token: Mapped[str | None] = mapped_column(String(400))

    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    is_trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Recorded as provenance on deliveries, never used to block capture.
    is_rooted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("user_id", "device_uid", name="uq_user_devices_user_id_device_uid"),
        enum_check("platform", DevicePlatform),
    )


class PasswordResetToken(BaseModel):
    """Single-use, short-lived, stored as an HMAC only."""

    __tablename__ = "password_reset_tokens"

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_ip: Mapped[str | None] = mapped_column(INET)

    __table_args__ = (UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),)

    @property
    def is_used(self) -> bool:
        return self.used_at is not None


__all__ = ["PasswordResetToken", "User", "UserDevice", "UserSession"]

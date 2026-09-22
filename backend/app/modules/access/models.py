"""Access-control ORM models: permissions, roles and scoped grants.

A user holds *grants*; each grant pairs a role (a bundle of permissions) with a
scope (global, company, project, site or department). An action is allowed when
the user holds some grant whose role contains the required permission and whose
scope covers the row being touched. See docs/03-rbac.md.

Deliberately not ABAC: "Site Manager, at Green Valley Site 2" is something an
operations manager can reason about and an auditor can verify.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constraints import enum_check
from app.core.db import BaseModel, CompanyModel
from app.modules.access.domain.enums import ScopeType


class Permission(BaseModel):
    """A single capability, named `module.action`.

    Global rather than company-scoped: the catalogue is part of the software,
    not of a customer's configuration. Seeded and kept in step by a test that
    compares the table against the code's catalogue.
    """

    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(80), nullable=False)
    module: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    # True for permissions only a Super Administrator may ever hold.
    is_restricted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (UniqueConstraint("code", name="uq_permissions_code"),)

    def __str__(self) -> str:
        return self.code


class Role(CompanyModel):
    """A named bundle of permissions.

    System roles are seeded and may be edited (except Super Administrator), so
    a customer can tighten or loosen a role without a code change.
    """

    __tablename__ = "roles"
    __audited__ = True

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(400))

    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Super Administrator: cannot be edited or deleted, only assigned.
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_assignable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # A role whose permissions are all reads. Enforced by a service-level guard
    # as well, so the Auditor role is read-only twice over.
    is_read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # The scope levels this role makes sense at, so the UI cannot offer
    # "Site Manager, company-wide" or "Finance Manager, at one site".
    allowed_scope_types: Mapped[list[str]] = mapped_column(
        ARRAY(String(20)), nullable=False, default=list
    )

    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_roles_company_id_code"),)

    permissions: Mapped[list[RolePermission]] = relationship(
        back_populates="role", lazy="selectin", cascade="all, delete-orphan"
    )


class RolePermission(BaseModel):
    __tablename__ = "role_permissions"

    role_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    permission_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "role_id", "permission_id", name="uq_role_permissions_role_id_permission_id"
        ),
    )

    role: Mapped[Role] = relationship(back_populates="permissions", lazy="noload")
    permission: Mapped[Permission] = relationship(lazy="joined")


class UserRoleGrant(CompanyModel):
    """Role + scope, granted to a user for a period.

    `scope_id` is intentionally not a foreign key: it points at a project, site
    or department depending on `scope_type`, and a polymorphic FK cannot be
    declared. Referential integrity is enforced in the service layer, and a
    nightly reconciliation job reports orphans.
    """

    __tablename__ = "user_role_grants"
    __audited__ = True

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    scope_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ScopeType.COMPANY.value
    )
    scope_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))

    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)

    granted_by_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    grant_reason: Mapped[str | None] = mapped_column(String(300))

    revoked_at: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        # One grant per (user, role, scope). Re-granting updates the dates.
        Index(
            "uq_user_role_grants_identity",
            "user_id",
            "role_id",
            "scope_type",
            "scope_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        enum_check("scope_type", ScopeType),
        # GLOBAL and COMPANY grants have no scope_id; the others require one.
        CheckConstraint(
            "(scope_type IN ('GLOBAL', 'COMPANY') AND scope_id IS NULL) "
            "OR (scope_type NOT IN ('GLOBAL', 'COMPANY') AND scope_id IS NOT NULL)",
            name="scope_id_matches_scope_type",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="validity_dates_ordered",
        ),
        Index("ix_user_role_grants_scope", "scope_type", "scope_id"),
    )

    role: Mapped[Role] = relationship(lazy="selectin")


__all__ = ["Permission", "Role", "RolePermission", "UserRoleGrant"]

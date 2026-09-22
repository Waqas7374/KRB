"""The resolved access context — shared kernel.

Lives in `core` rather than in the access module because every repository in
every module needs the type, while only the access module knows how to build
one from the database. Pure data: no SQLAlchemy, no I/O.

Semantics, from docs/03-rbac.md:

    GLOBAL       covers everything
    COMPANY:c    covers rows with company_id = c
    PROJECT:p    covers rows with project_id = p, and the sites under it
    SITE:s       covers rows with site_id = s
    DEPARTMENT:d covers rows with department_id = d

Rows with no project or site (chart of accounts, materials, vendors) are covered
by COMPANY or GLOBAL grants only — which is why a Site Manager cannot edit the
material master.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.core.errors import PermissionDeniedError


@dataclass(frozen=True, slots=True)
class ScopeSet:
    """The reach of one permission, unioned across all of a user's grants."""

    is_global: bool = False
    company_ids: frozenset[UUID] = field(default_factory=frozenset)
    project_ids: frozenset[UUID] = field(default_factory=frozenset)
    site_ids: frozenset[UUID] = field(default_factory=frozenset)
    department_ids: frozenset[UUID] = field(default_factory=frozenset)
    # Projects the holder may see as *records* without being scoped into their
    # contents. A site-scoped user needs to read the project their site belongs
    # to; giving them `project_ids` instead would hand them every sibling site
    # in that project, which is a privilege escalation dressed as convenience.
    parent_project_ids: frozenset[UUID] = field(default_factory=frozenset)

    @property
    def is_company_wide(self) -> bool:
        """True when no narrowing predicate is needed beyond company_id."""
        return self.is_global or bool(self.company_ids)

    @property
    def is_empty(self) -> bool:
        return not (
            self.is_global
            or self.company_ids
            or self.project_ids
            or self.site_ids
            or self.department_ids
            or self.parent_project_ids
        )

    def merge(self, other: ScopeSet) -> ScopeSet:
        return ScopeSet(
            is_global=self.is_global or other.is_global,
            company_ids=self.company_ids | other.company_ids,
            project_ids=self.project_ids | other.project_ids,
            site_ids=self.site_ids | other.site_ids,
            department_ids=self.department_ids | other.department_ids,
            parent_project_ids=self.parent_project_ids | other.parent_project_ids,
        )

    @property
    def visible_project_ids(self) -> frozenset[UUID]:
        """Projects readable as records, scoped-into or merely parent."""
        return self.project_ids | self.parent_project_ids

    def covers(
        self,
        *,
        company_id: UUID | None = None,
        project_id: UUID | None = None,
        site_id: UUID | None = None,
        department_id: UUID | None = None,
    ) -> bool:
        """Whether this scope reaches a row with the given dimensions.

        A row is covered if *any* of the grant's levels matches. Company-wide
        grants cover everything inside their company; narrower grants require
        the corresponding dimension to be present on the row.
        """
        if self.is_global:
            return True
        return (
            (company_id is not None and company_id in self.company_ids)
            or (project_id is not None and project_id in self.project_ids)
            or (site_id is not None and site_id in self.site_ids)
            or (department_id is not None and department_id in self.department_ids)
        )


_EMPTY_SCOPE = ScopeSet()


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Everything authorisation needs about the caller, resolved once per request."""

    user_id: UUID
    company_id: UUID
    company_ids: frozenset[UUID]
    permissions: frozenset[str]
    scopes: dict[str, ScopeSet]
    permissions_version: int
    is_superuser: bool = False
    # True when every role the user holds is read-only. Checked by middleware so
    # the Auditor is prevented from writing twice over: by lacking write
    # permissions, and by this flag.
    is_read_only: bool = False
    role_codes: frozenset[str] = field(default_factory=frozenset)

    # --- Permission checks ---------------------------------------------------

    def has(self, permission: str) -> bool:
        return self.is_superuser or permission in self.permissions

    def has_any(self, *permissions: str) -> bool:
        return any(self.has(p) for p in permissions)

    def has_all(self, *permissions: str) -> bool:
        return all(self.has(p) for p in permissions)

    def require(self, permission: str) -> None:
        if not self.has(permission):
            raise PermissionDeniedError(permission)

    # --- Scope checks --------------------------------------------------------

    def scope_for(self, permission: str) -> ScopeSet:
        if self.is_superuser:
            return ScopeSet(is_global=True)
        return self.scopes.get(permission, _EMPTY_SCOPE)

    def can(
        self,
        permission: str,
        *,
        company_id: UUID | None = None,
        project_id: UUID | None = None,
        site_id: UUID | None = None,
        department_id: UUID | None = None,
    ) -> bool:
        """Permission **and** scope, in one call."""
        if not self.has(permission):
            return False
        return self.scope_for(permission).covers(
            company_id=company_id if company_id is not None else self.company_id,
            project_id=project_id,
            site_id=site_id,
            department_id=department_id,
        )

    def accessible_project_ids(self, permission: str) -> frozenset[UUID] | None:
        """Project ids reachable with a permission, or None meaning "all"."""
        scope = self.scope_for(permission)
        if scope.is_company_wide:
            return None
        return scope.project_ids

    def accessible_site_ids(self, permission: str) -> frozenset[UUID] | None:
        scope = self.scope_for(permission)
        if scope.is_company_wide:
            return None
        return scope.site_ids


def system_access_context(company_id: UUID, user_id: UUID) -> AccessContext:
    """An unrestricted context for seeders and background jobs.

    Never produced from a request. Writes made with it are marked in the audit
    log by `actor_label`, so machine changes are distinguishable from human ones.
    """
    return AccessContext(
        user_id=user_id,
        company_id=company_id,
        company_ids=frozenset({company_id}),
        permissions=frozenset(),
        scopes={},
        permissions_version=0,
        is_superuser=True,
        role_codes=frozenset({"SYSTEM"}),
    )

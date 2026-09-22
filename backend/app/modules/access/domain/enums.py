"""Access-control scope semantics."""

from __future__ import annotations

from enum import StrEnum


class ScopeType(StrEnum):
    """How wide a grant reaches.

    Ordered GLOBAL > COMPANY > PROJECT > SITE > DEPARTMENT. A grant covers a row
    when the grant's scope is the row's scope or an ancestor of it.
    """

    GLOBAL = "GLOBAL"
    COMPANY = "COMPANY"
    PROJECT = "PROJECT"
    SITE = "SITE"
    DEPARTMENT = "DEPARTMENT"

    @property
    def breadth(self) -> int:
        """Higher means wider. Used to pick the most permissive grant."""
        return _BREADTH[self]

    @property
    def needs_scope_id(self) -> bool:
        return self not in {ScopeType.GLOBAL, ScopeType.COMPANY}

    @property
    def target_table(self) -> str | None:
        """Which table `scope_id` points at, for referential validation."""
        return {
            ScopeType.PROJECT: "projects",
            ScopeType.SITE: "sites",
            ScopeType.DEPARTMENT: "departments",
        }.get(self)


_BREADTH: dict[ScopeType, int] = {
    ScopeType.GLOBAL: 4,
    ScopeType.COMPANY: 3,
    ScopeType.PROJECT: 2,
    ScopeType.SITE: 1,
    ScopeType.DEPARTMENT: 1,
}

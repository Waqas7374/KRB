"""SQL-level scope filtering.

Authorisation is applied as a WHERE predicate, never as a filter over results
that have already been fetched. Post-filtering would make every `total`, every
aggregate and every export wrong — a screen reporting "247 results" while
showing 12 is worse than no filter at all.

Usage in a repository::

    stmt = select(Delivery)
    stmt = scope_filter(stmt, Delivery, ctx, "deliveries.view")

The model declares which columns carry which dimension, so the predicate is
built from the model rather than hard-coded per query.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from sqlalchemy import Select, false, or_

from app.core.access import AccessContext
from app.core.errors import PermissionDeniedError

_T = TypeVar("_T")


class ScopedModel(Protocol):
    """A model that can be scope-filtered.

    Every company-scoped model satisfies this by having `company_id`; models
    that also carry `project_id`, `site_id` or `department_id` are narrowed
    further. The attributes are looked up dynamically because most models have
    only some of them.
    """

    company_id: Any


def _column(model: type[Any], name: str) -> Any | None:
    column = getattr(model, name, None)
    # Guard against a same-named Python property rather than a mapped column.
    if column is None or not hasattr(column, "in_"):
        return None
    return column


def scope_filter(
    stmt: Select[Any],
    model: type[Any],
    ctx: AccessContext,
    permission: str,
    *,
    require_permission: bool = True,
) -> Select[Any]:
    """Narrow a statement to the rows `ctx` may see with `permission`.

    Raises `PermissionDeniedError` when the caller lacks the permission outright
    — that is a 403, distinct from "has the permission but this row is out of
    scope", which yields no rows and therefore a 404.
    """
    if require_permission and not ctx.has(permission):
        raise PermissionDeniedError(permission)

    scope = ctx.scope_for(permission)

    if scope.is_global:
        return stmt

    # Company is the outermost boundary and applies even to a global-ish grant
    # in a multi-company deployment.
    company_column = _column(model, "company_id")
    if company_column is not None:
        stmt = stmt.where(company_column.in_(ctx.company_ids))

    if scope.is_company_wide:
        return stmt

    # Company-wide reference data (the material catalogue, units, vendors)
    # declares `__scope_company_wide__`. It belongs to no project or site, so a
    # project- or site-scoped grant of its view permission means "may see the
    # catalogue", not "may see none of it". Without this, a site manager held
    # `materials.view` yet saw zero materials and could not raise a purchase
    # request or, later, capture a delivery.
    if getattr(model, "__scope_company_wide__", False):
        return stmt

    # A narrower grant: the row must match at least one dimension the user was
    # granted. Unmatched dimensions contribute nothing rather than everything.
    clauses = []

    # Some tables *are* a dimension rather than carrying one: `projects` has no
    # project_id, `sites` has no site_id — their own id is the dimension. A
    # model declares that with `__scope_self__`, and without this a
    # project-scoped user would see no projects at all.
    self_dimension = getattr(model, "__scope_self__", None)
    if self_dimension is not None:
        id_column = _column(model, "id")
        if id_column is not None:
            if self_dimension == "PROJECT" and scope.visible_project_ids:
                clauses.append(id_column.in_(scope.visible_project_ids))
            elif self_dimension == "SITE" and scope.site_ids:
                clauses.append(id_column.in_(scope.site_ids))
            elif self_dimension == "DEPARTMENT" and scope.department_ids:
                clauses.append(id_column.in_(scope.department_ids))

    project_column = _column(model, "project_id")
    if project_column is not None and scope.project_ids:
        clauses.append(project_column.in_(scope.project_ids))

    site_column = _column(model, "site_id")
    if site_column is not None and scope.site_ids:
        clauses.append(site_column.in_(scope.site_ids))

    department_column = _column(model, "department_id")
    if department_column is not None and scope.department_ids:
        clauses.append(department_column.in_(scope.department_ids))

    if not clauses:
        # The user holds the permission only at a scope this table cannot
        # express — e.g. a site-scoped grant against the material master. That
        # is no access, not full access.
        return stmt.where(false())

    return stmt.where(or_(*clauses))


def assert_in_scope(
    ctx: AccessContext,
    permission: str,
    *,
    company_id: Any = None,
    project_id: Any = None,
    site_id: Any = None,
    department_id: Any = None,
    entity: str = "Resource",
) -> None:
    """Check a single already-loaded row.

    Used after fetching by id when the fetch did not apply `scope_filter`.
    Raises `NotFoundError`, not `PermissionDeniedError`: confirming that a
    record exists leaks vendor lists and document numbering sequences.
    """
    from app.core.errors import NotFoundError

    if not ctx.has(permission):
        raise PermissionDeniedError(permission)

    covered = ctx.scope_for(permission).covers(
        company_id=company_id,
        project_id=project_id,
        site_id=site_id,
        department_id=department_id,
    )
    if not covered:
        raise NotFoundError(entity)

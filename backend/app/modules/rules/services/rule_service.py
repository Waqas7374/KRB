"""Create, change and retire business rules.

A rule's *type*, *scope* and *start date* say what it is; changing them would
rewrite what earlier documents were judged against. So they are fixed at
creation. What may change is the value, condition, priority and end date — and
the change is audited, while every flag already raised keeps its own snapshot.
To change what a rule applies to, end it and create another.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import conditions
from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import BusinessRuleError, ValidationError, VersionConflictError
from app.core.pagination import PageParams
from app.modules.rules.domain.enums import RuleType
from app.modules.rules.domain.rule_types import RULE_TYPES, validate_value
from app.modules.rules.models import BusinessRule

PERM_VIEW = "settings.view"
PERM_MANAGE = "settings.manage_rules"

# Scope keys whose value must be an id; `doc_type` is a document-type code.
_ID_KEYS = frozenset(
    {
        "project_id",
        "site_id",
        "material_id",
        "material_category_id",
        "vendor_id",
        "truck_type_id",
        "role_id",
    }
)


def repository(session: AsyncSession) -> ScopedRepository[BusinessRule]:
    return ScopedRepository(
        session,
        BusinessRule,
        entity_name="Business rule",
        sortable={"rule_type", "priority", "effective_from", "created_at", "updated_at"},
        searchable=("name", "description"),
        default_sort="rule_type",
    )


@dataclass(frozen=True, slots=True)
class RuleInput:
    rule_type: RuleType
    name: str | None
    description: str | None
    scope: dict[str, str]
    condition: Any
    value: dict[str, Any]
    priority: int
    effective_from: date
    effective_to: date | None
    is_active: bool = True


def _fail(field: str, message: str) -> ValidationError:
    return ValidationError(
        message, errors=[{"field": field, "code": "invalid", "message": message}]
    )


def _check_scope(rule_type: RuleType, scope: dict[str, str]) -> dict[str, str]:
    spec = RULE_TYPES[rule_type]
    unknown = set(scope) - spec.scope_keys
    if unknown:
        raise _fail(
            "scope",
            f"{spec.label} cannot be scoped by {', '.join(sorted(unknown))}. "
            f"It may be scoped by: {', '.join(sorted(spec.scope_keys))}.",
        )
    for key, value in scope.items():
        if not value:
            raise _fail(f"scope.{key}", "must not be empty")
        if key in _ID_KEYS:
            try:
                UUID(value)
            except ValueError as exc:
                raise _fail(f"scope.{key}", "must be an id") from exc
    return {k: str(v) for k, v in scope.items()}


def _check_value_and_condition(
    rule_type: RuleType, value: dict[str, Any], condition: Any
) -> dict[str, Any]:
    try:
        stored = validate_value(rule_type, value)
    except Exception as exc:
        raise _fail("value", _humanise(exc)) from exc
    if condition is not None:
        try:
            conditions.validate(condition)
        except conditions.ConditionError as exc:
            raise _fail("condition", str(exc)) from exc
    return stored


def _humanise(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        parts = [
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" if e.get("loc") else e["msg"]
            for e in errors()
        ]
        return "; ".join(parts)
    return str(exc)


def _overlaps(a_from: date, a_to: date | None, b_from: date, b_to: date | None) -> bool:
    return (a_to is None or a_to >= b_from) and (b_to is None or b_to >= a_from)


async def _assert_no_conflict(
    session: AsyncSession,
    company_id: UUID,
    data: RuleInput,
    *,
    exclude_id: UUID | None = None,
) -> None:
    """Two active rules with the same type, scope, priority and condition over
    overlapping dates would tie, and the winner would be arbitrary."""
    rows = await session.execute(
        select(BusinessRule).where(
            BusinessRule.company_id == company_id,
            BusinessRule.rule_type == data.rule_type.value,
            BusinessRule.is_active.is_(True),
            BusinessRule.priority == data.priority,
        )
    )
    for other in rows.scalars():
        if other.id == exclude_id:
            continue
        if (
            dict(other.scope or {}) == data.scope
            and other.condition == data.condition
            and _overlaps(
                other.effective_from, other.effective_to, data.effective_from, data.effective_to
            )
        ):
            label = other.name or RULE_TYPES[data.rule_type].label
            raise BusinessRuleError(
                "business_rule_conflict",
                f"“{label}” already covers this scope for these dates at the same priority, so "
                "the two would tie. End that rule first, change the priority, or narrow the dates.",
            )


async def create(session: AsyncSession, ctx: AccessContext, data: RuleInput) -> BusinessRule:
    scope = _check_scope(data.rule_type, data.scope)
    value = _check_value_and_condition(data.rule_type, data.value, data.condition)
    if data.effective_to is not None and data.effective_to < data.effective_from:
        raise _fail("effective_to", "cannot be before the start date")
    normalised = RuleInput(
        data.rule_type,
        data.name,
        data.description,
        scope,
        data.condition,
        value,
        data.priority,
        data.effective_from,
        data.effective_to,
        data.is_active,
    )
    if data.is_active:
        await _assert_no_conflict(session, ctx.company_id, normalised)
    rule = BusinessRule(
        company_id=ctx.company_id,
        rule_type=data.rule_type.value,
        name=data.name,
        description=data.description,
        scope=scope,
        condition=data.condition,
        value=value,
        priority=data.priority,
        effective_from=data.effective_from,
        effective_to=data.effective_to,
        is_active=data.is_active,
        created_by_id=ctx.user_id,
    )
    session.add(rule)
    await session.flush()
    return rule


@dataclass(frozen=True, slots=True)
class RuleChange:
    """The parts of a rule that may change. `condition_set` distinguishes
    "clear the condition" from "leave it alone"."""

    name: str | None = None
    description: str | None = None
    value: dict[str, Any] | None = None
    condition: Any = None
    condition_set: bool = False
    priority: int | None = None
    effective_to: date | None = None
    effective_to_set: bool = False
    is_active: bool | None = None


async def _get_for_update(session: AsyncSession, ctx: AccessContext, rule_id: UUID) -> BusinessRule:
    return await repository(session).get_for_update(ctx, PERM_MANAGE, rule_id)


async def update(
    session: AsyncSession,
    ctx: AccessContext,
    rule_id: UUID,
    change: RuleChange,
    *,
    expected_version: int | None,
) -> BusinessRule:
    rule = await _get_for_update(session, ctx, rule_id)
    if expected_version is not None and rule.version != expected_version:
        raise VersionConflictError(
            f"This rule was changed by someone else (version {rule.version}, "
            f"you had {expected_version}). Reload and try again."
        )
    rule_type = RuleType(rule.rule_type)
    condition = change.condition if change.condition_set else rule.condition
    value = _check_value_and_condition(
        rule_type, change.value if change.value is not None else dict(rule.value), condition
    )
    effective_to = change.effective_to if change.effective_to_set else rule.effective_to
    if effective_to is not None and effective_to < rule.effective_from:
        raise _fail("effective_to", "cannot be before the start date")
    priority = rule.priority if change.priority is None else change.priority
    active = rule.is_active if change.is_active is None else change.is_active

    if active:
        await _assert_no_conflict(
            session,
            ctx.company_id,
            RuleInput(
                rule_type,
                rule.name,
                rule.description,
                dict(rule.scope or {}),
                condition,
                dict(value),
                priority,
                rule.effective_from,
                effective_to,
            ),
            exclude_id=rule.id,
        )
    rule.name = rule.name if change.name is None else change.name
    rule.description = rule.description if change.description is None else change.description
    rule.value = value
    rule.condition = condition
    rule.priority = priority
    rule.effective_to = effective_to
    rule.is_active = active
    rule.updated_by_id = ctx.user_id
    rule.version += 1
    await session.flush()
    return rule


async def list_rules(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[BusinessRule], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, rule_id: UUID) -> BusinessRule:
    return await repository(session).get(ctx, PERM_VIEW, rule_id)

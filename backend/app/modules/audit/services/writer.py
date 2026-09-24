"""The audit write path.

Two ways in, both landing in the same table:

1.  **Automatic** — a session hook diffs every model marked `__audited__` before
    flush and records what changed. Catches everything, including changes made
    through a path nobody remembered to instrument.
2.  **Explicit** — a service calls `record()` for a business event that needs a
    human-readable summary or an action verb the diff cannot infer
    ("approved", "rate changed from 48 to 52").

Both run inside the caller's transaction, so an audit row can never exist for a
change that rolled back, and a committed change can never lack its audit row.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.context import current_context
from app.core.db import Base
from app.core.types import utcnow, uuid7
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.models import AuditLog

# Never written to the audit log, whatever a model declares.
GLOBAL_EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {
        "password_hash",
        "refresh_token_hash",
        "token_hash",
        "push_token",
        "access_token",
        "secret",
        "updated_at",
        "created_at",
        "version",
        "updated_by_id",
    }
)

# Fields whose values are recorded as a mask rather than in clear, because the
# fact of the change matters and the value is sensitive.
MASKED_FIELDS: frozenset[str] = frozenset({"account_no", "iban", "national_id", "cnic"})


def _jsonable(value: Any) -> Any:
    """Coerce a column value into something JSONB will accept."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        # Money and quantities are kept as strings so JSON round-tripping
        # cannot silently turn 12.5000 into a float.
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _mask(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return f"***{text[-4:]}" if len(text) > 4 else "****"


def _label_of(instance: Base) -> str | None:
    """A human-readable identity for the row.

    Tried in order of usefulness to someone reading the log years later.
    """
    for attribute in (
        "po_number",
        "pr_number",
        "grn_number",
        "delivery_number",
        "invoice_number",
        "code",
        "name",
        "full_name",
        "legal_name",
        "file_name",
    ):
        value = getattr(instance, attribute, None)
        if value:
            return str(value)[:200]
    return None


def _dimension(instance: Base, name: str) -> UUID | None:
    value = getattr(instance, name, None)
    return value if isinstance(value, UUID) else None


def _audited_fields(instance: Base) -> frozenset[str]:
    excluded = GLOBAL_EXCLUDED_FIELDS | frozenset(getattr(instance, "__audit_exclude__", ()))
    mapper = inspect(type(instance))
    return frozenset(column.key for column in mapper.column_attrs) - excluded


def build_rows_for_flush(session: Session) -> list[dict[str, Any]]:
    """Diff the session's pending changes into audit-row dictionaries.

    Called from the `before_flush` hook. Returns plain dicts rather than ORM
    objects so they can be bulk-inserted after the flush without re-entering
    the same hook.
    """
    ctx = current_context()
    now = utcnow()
    rows: list[dict[str, Any]] = []

    def base_row(instance: Base, action: AuditAction) -> dict[str, Any]:
        return {
            "id": uuid7(),
            "company_id": _dimension(instance, "company_id"),
            "actor_user_id": ctx.user_id,
            "actor_name": ctx.actor_name,
            "actor_roles": list(ctx.actor_roles) if ctx.actor_roles else None,
            "actor_label": ctx.actor_label,
            "action": action.value,
            "entity_type": type(instance).__name__,
            "entity_id": getattr(instance, "id", None),
            "entity_label": _label_of(instance),
            "ip": ctx.ip,
            "user_agent": ctx.user_agent,
            "device_id": ctx.device_id,
            "request_id": ctx.request_id if ctx.request_id != "-" else None,
            "project_id": _dimension(instance, "project_id"),
            "site_id": _dimension(instance, "site_id"),
            "occurred_at": now,
        }

    for instance in session.new:
        if not getattr(instance, "__audited__", False):
            continue
        fields = _audited_fields(instance)
        values = {
            field: (
                _mask(getattr(instance, field, None))
                if field in MASKED_FIELDS
                else _jsonable(getattr(instance, field, None))
            )
            for field in fields
        }
        rows.append(
            base_row(instance, AuditAction.CREATE)
            | {"new_values": values, "changed_fields": sorted(values)}
        )

    for instance in session.dirty:
        if not getattr(instance, "__audited__", False):
            continue
        if not session.is_modified(instance, include_collections=False):
            continue

        state = inspect(instance)
        old_values: dict[str, Any] = {}
        new_values: dict[str, Any] = {}

        for field in _audited_fields(instance):
            history = state.attrs[field].history
            if not history.has_changes():
                continue
            before = history.deleted[0] if history.deleted else None
            after = history.added[0] if history.added else None
            if before == after:
                continue
            if field in MASKED_FIELDS:
                old_values[field] = _mask(before)
                new_values[field] = _mask(after)
            else:
                old_values[field] = _jsonable(before)
                new_values[field] = _jsonable(after)

        if not new_values:
            continue

        # A soft delete is a business event, not an ordinary field change.
        action = (
            AuditAction.SOFT_DELETE
            if "deleted_at" in new_values and new_values["deleted_at"] is not None
            else AuditAction.RESTORE
            if "deleted_at" in new_values and new_values["deleted_at"] is None
            else AuditAction.UPDATE
        )

        rows.append(
            base_row(instance, action)
            | {
                "old_values": old_values,
                "new_values": new_values,
                "changed_fields": sorted(new_values),
            }
        )

    return rows


async def record(
    session: AsyncSession,
    *,
    action: AuditAction,
    entity_type: str,
    entity_id: UUID | None = None,
    entity_label: str | None = None,
    company_id: UUID | None = None,
    summary: str | None = None,
    old_values: dict[str, Any] | None = None,
    new_values: dict[str, Any] | None = None,
    project_id: UUID | None = None,
    site_id: UUID | None = None,
    actor_user_id: UUID | None = None,
) -> AuditLog:
    """Record a business-level audit entry explicitly.

    Used where the automatic diff cannot express what happened: an approval, a
    rejection with a reason, a rate supersession, a login. `summary` should read
    as a sentence — "Crush / Shree Stone: 48.0000 -> 52.0000 effective 12 Aug".
    """
    ctx = current_context()
    # The context's name/roles describe the request's user; they only apply
    # when the entry is about that same actor (a login passes its own id
    # before any request user exists).
    same_actor = actor_user_id is None or actor_user_id == ctx.user_id
    entry = AuditLog(
        id=uuid7(),
        company_id=company_id or ctx.company_id,
        actor_user_id=actor_user_id or ctx.user_id,
        actor_name=ctx.actor_name if same_actor else None,
        actor_roles=list(ctx.actor_roles) if same_actor and ctx.actor_roles else None,
        actor_label=ctx.actor_label,
        action=action.value,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_label=entity_label[:200] if entity_label else None,
        summary=summary[:500] if summary else None,
        old_values={k: _jsonable(v) for k, v in (old_values or {}).items()} or None,
        new_values={k: _jsonable(v) for k, v in (new_values or {}).items()} or None,
        changed_fields=sorted(new_values) if new_values else None,
        ip=ctx.ip,
        user_agent=ctx.user_agent,
        device_id=ctx.device_id,
        request_id=ctx.request_id if ctx.request_id != "-" else None,
        project_id=project_id,
        site_id=site_id,
        occurred_at=utcnow(),
    )
    session.add(entry)
    return entry

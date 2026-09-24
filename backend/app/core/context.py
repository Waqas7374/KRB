"""Request-scoped context.

Holds the acting user, company, request id and client fingerprint so that the
audit writer and the logger can reach them without threading arguments through
every call. Populated by middleware; read by the unit of work, the audit
subsystem and structlog.

Deliberately narrow: this is not a place to stash business state. Anything a
service needs to *decide* something is passed explicitly.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    user_id: UUID | None = None
    company_id: UUID | None = None
    ip: str | None = None
    user_agent: str | None = None
    device_id: str | None = None
    app_version: str | None = None
    # Set for work performed by a background worker rather than a user request.
    actor_label: str | None = None
    # Snapshot of who the actor was at the time, copied onto every audit row
    # so the trail still reads correctly after a rename or a role change.
    actor_name: str | None = None
    actor_roles: tuple[str, ...] | None = None

    @property
    def is_system(self) -> bool:
        return self.user_id is None


_EMPTY = RequestContext(request_id="-", actor_label="system")

_context: contextvars.ContextVar[RequestContext] = contextvars.ContextVar(
    "request_context", default=_EMPTY
)


def current_context() -> RequestContext:
    return _context.get()


def set_context(ctx: RequestContext) -> contextvars.Token[RequestContext]:
    return _context.set(ctx)


def reset_context(token: contextvars.Token[RequestContext]) -> None:
    _context.reset(token)


def update_context(**fields: object) -> None:
    """Merge fields into the current context (e.g. user_id once authenticated)."""
    _context.set(replace(_context.get(), **fields))  # type: ignore[arg-type]


@contextmanager
def system_context(label: str, company_id: UUID | None = None) -> Iterator[RequestContext]:
    """Run a block as the system actor — used by Celery tasks and seeders.

    Audit rows written inside carry `actor_label` instead of a user id, so
    machine-originated changes are never mistaken for human ones.
    """
    ctx = RequestContext(request_id=f"system:{label}", company_id=company_id, actor_label=label)
    token = set_context(ctx)
    try:
        yield ctx
    finally:
        reset_context(token)


def require_user_id() -> UUID:
    """Return the acting user id, or raise if there is none.

    Used on write paths that must be attributable to a person.
    """
    user_id = current_context().user_id
    if user_id is None:
        raise RuntimeError(
            "No acting user in context; this operation must be attributable. "
            "Wrap background work in system_context() if it is genuinely machine-originated."
        )
    return user_id

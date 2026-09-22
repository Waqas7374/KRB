"""Session hook that writes audit rows automatically.

Installed once at application start (and at worker start). Lives in the audit
module rather than in `core.db` so that `core` does not depend on a module —
the layering rule is enforced in CI, and this is the seam that keeps it true.

The hook runs on `before_flush`, which is the only point where both the old and
the new value of every changed column are still available.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.audit.models import AuditLog
from app.modules.audit.services.writer import build_rows_for_flush

log = get_logger("audit")

_INSTALLED = False


def install_audit_hooks() -> None:
    """Register the audit listener. Idempotent."""
    global _INSTALLED
    if _INSTALLED:
        return

    @event.listens_for(Session, "before_flush")
    def _write_audit_rows(session: Session, _flush_context: Any, _instances: Any) -> None:
        # Guard against recursion: adding AuditLog rows below triggers another
        # before_flush, and that pass must not try to audit the audit rows.
        if session.info.get("_auditing"):
            return
        session.info["_auditing"] = True
        try:
            rows = build_rows_for_flush(session)
            for row in rows:
                session.add(AuditLog(**row))
        except Exception:
            # Deliberately re-raised. If the audit row cannot be written, the
            # whole transaction must fail: committing an unaudited change to a
            # financial or inventory record is worse than refusing the change.
            log.error("audit.hook_failed", exc_info=True)
            raise
        finally:
            session.info["_auditing"] = False

    _INSTALLED = True
    log.info("audit.hooks_installed")

"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Created: ${create_date}

Checklist before committing this migration:
  - Review every generated statement by hand; autogenerate is a draft.
  - No table rewrite on a hot table (deliveries, inventory_transactions,
    journal_entry_lines, audit_logs).
  - New indexes on large tables use CREATE INDEX CONCURRENTLY, which requires
    running outside a transaction (see the op.get_context() note below).
  - Column changes follow expand -> deploy -> contract across two releases.
  - downgrade() is implemented, or a comment explains why it cannot be.
"""

from __future__ import annotations

from collections.abc import Sequence

# Imported unconditionally: needed whenever a migration touches a geography column.
import geoalchemy2
import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}

"""add version to users and roles; add users and roles admin

Revision ID: abdbd88f57ce
Revises: 1df44acb8972
Created: 2026-09-22 13:47:47.430986+00:00

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

revision: str = "abdbd88f57ce"
down_revision: str | None = "1df44acb8972"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default backfills every existing row to version 1 in the same
    # statement, then is dropped so future inserts must supply a value (the
    # model default is Python-side, matching every other MasterDataModel
    # table's version column).
    op.add_column(
        "roles", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )
    op.alter_column("roles", "version", server_default=None)
    op.add_column(
        "users", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )
    op.alter_column("users", "version", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "version")
    op.drop_column("roles", "version")

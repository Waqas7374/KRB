"""enable required postgresql extensions

Revision ID: da8b9fdc2b5a
Revises:
Created: 2026-09-22 00:41:50.828457+00:00

The local Docker image also runs infra/postgres/init/01-extensions.sql on first
boot, but that only fires for a brand-new data directory. This migration makes
the same guarantee for a managed PostgreSQL instance, a restored backup, or any
database created outside Compose.

Extensions are created IF NOT EXISTS, so this is safe to re-run. It is NOT
reversed on downgrade: dropping an extension would cascade away every column
that depends on it, which is never what a rollback intends.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "da8b9fdc2b5a"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS: tuple[tuple[str, str], ...] = (
    ("postgis", "site geofences and delivery GPS points"),
    ("pg_trgm", "fuzzy search on names, codes and truck plates"),
    ("btree_gist", "exclusion constraints on effective-dated vendor rates"),
    ("citext", "case-insensitive emails and codes"),
    ("ltree", "chart-of-accounts and material-category trees"),
    ("pgcrypto", "digests for attachment checksums"),
)


def upgrade() -> None:
    for name, _purpose in EXTENSIONS:
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {name}")


def downgrade() -> None:
    # Intentionally not reversed: DROP EXTENSION postgis CASCADE would silently
    # delete every geography column in the schema.
    pass

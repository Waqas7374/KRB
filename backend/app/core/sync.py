"""The change cursor behind mobile pull (docs/06 §4).

A reference table that a phone caches carries `server_seq`, drawn from one
global sequence and bumped by a database trigger on every insert and update. A
device remembers the highest `server_seq` it has seen and asks for what is
newer. Because the sequence is global and monotonic, a page boundary never
skips a row, and it does not matter that two rows changed in the same second.

The application never writes the column — the trigger does — so there is no
`server_default` here on purpose: a trigger is the only thing that also fires
on UPDATE, and declaring a default the migration does not own would make
`alembic check` disagree with the database.

A module that owns such a table exposes a `sync_feed` service returning
`FeedRow`s; the sync module merges them without ever touching another module's
tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

# The Postgres sequence and trigger function are created by the migration.
SEQUENCE = "global_change_seq"


class SyncSeqMixin:
    """Adds the change cursor. Filled by the database, never by the application."""

    server_seq: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)


@dataclass(frozen=True, slots=True)
class FeedRow:
    entity: str
    id: UUID
    seq: int
    # A soft-deleted (or no-longer-relevant) row arrives as a tombstone, so the
    # device can drop it from its cache.
    deleted: bool = False
    data: dict[str, Any] = field(default_factory=dict)

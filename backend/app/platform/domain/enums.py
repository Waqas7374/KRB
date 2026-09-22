"""Platform infrastructure enumerations."""

from __future__ import annotations

from enum import StrEnum


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    RETRYING = "RETRYING"
    PROCESSED = "PROCESSED"
    # Retries exhausted. Never deleted — a dead event is evidence that
    # something did not happen, which someone needs to see.
    DEAD = "DEAD"

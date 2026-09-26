"""Sync API contract (docs/06 §4).

Results are **per operation, never all-or-nothing**: one bad entry must not stop
forty good ones behind it on a 2G connection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MAX_OPS = 50


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SyncOp(ApiModel):
    # UUIDv7 on the device; the idempotency key of this one operation.
    op_id: Annotated[str, Field(min_length=8, max_length=64)]
    entity: Literal["delivery", "delivery_correction"]
    op: Literal["create", "update"]
    client_created_at: datetime | None = None
    payload: dict[str, Any]


class PushRequest(ApiModel):
    # The app's stable install id: the registry's `device_uid`.
    device_id: Annotated[str, Field(min_length=4, max_length=80)]
    app_version: Annotated[str | None, Field(max_length=30)] = None
    platform: Literal["ANDROID", "IOS", "WEB"] | None = None
    push_token: Annotated[str | None, Field(max_length=300)] = None
    network: Annotated[str | None, Field(max_length=20)] = None
    # The device's clock as it sends: how skew is measured, separately from how
    # long an entry waited offline.
    device_time: datetime | None = None
    ops: Annotated[list[SyncOp], Field(min_length=1, max_length=MAX_OPS)]


class OpError(ApiModel):
    code: str
    message: str
    retryable: bool
    fields: list[dict[str, str]] = []


class RecordFlag(ApiModel):
    type: str
    severity: str
    message: str


class RecordSummary(ApiModel):
    id: UUID
    delivery_number: str
    status: str
    flags: list[RecordFlag] = []
    # None for a person who may not see prices.
    rate: str | None = None
    amount: str | None = None


class OpResult(ApiModel):
    op_id: str
    # applied: recorded now · duplicate: already recorded, replayed as success ·
    # conflict: the server moved on and wins · rejected: permanent, fix or drop ·
    # deferred: a transient server problem, retry.
    outcome: Literal["applied", "duplicate", "conflict", "rejected", "deferred"]
    record: RecordSummary | None = None
    error: OpError | None = None


class PushResponse(ApiModel):
    server_time: datetime
    results: list[OpResult]


class PullEntity(ApiModel):
    entity: str
    id: UUID
    deleted: bool = False
    server_seq: int
    data: dict[str, Any] = {}


class PullResponse(ApiModel):
    server_time: datetime
    # Entities asked for that this person may not sync (no permission), so the
    # device can say so instead of showing an empty list.
    not_permitted: list[str] = []
    # Send this back as `since` to continue. Monotonic: a missed page is never
    # silently skipped.
    server_seq: int
    has_more: bool
    changes: list[PullEntity]

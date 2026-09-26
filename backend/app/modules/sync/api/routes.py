"""Mobile sync endpoints (docs/06 §4)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import Access, SessionDep, UowDep, require
from app.modules.sync.schemas import MAX_OPS, PullResponse, PushRequest, PushResponse
from app.modules.sync.services import pull as pull_service
from app.modules.sync.services import push as push_service

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post(
    "/push",
    response_model=PushResponse,
    dependencies=[require(push_service.PERM_PUSH)],
    summary="Send what was captured offline. Each entry gets its own outcome.",
    description=(
        f"Up to {MAX_OPS} operations per call. The result is per operation, never "
        "all-or-nothing: `applied`, `duplicate` (already recorded, treat as success), "
        "`conflict` (the server's version stands), `rejected` (permanent, fix or drop) "
        "or `deferred` (a transient server problem, retry). The request itself succeeds "
        "whenever the batch was understood."
    ),
)
async def push(payload: PushRequest, ctx: Access, uow: UowDep) -> PushResponse:
    return await push_service.push(uow.session, ctx, payload)


@router.get(
    "/pull",
    response_model=PullResponse,
    summary="What changed since the cursor: reference data the phone caches, with tombstones",
)
async def pull(
    ctx: Access,
    session: SessionDep,
    since: Annotated[int, Query(ge=0, description="The `server_seq` last received; 0 for all")] = 0,
    entities: Annotated[list[str] | None, Query(description="Default: everything")] = None,
    limit: Annotated[int, Query(ge=1, le=pull_service.MAX_LIMIT)] = pull_service.DEFAULT_LIMIT,
    site_id: UUID | None = None,
    device_id: Annotated[str | None, Query(max_length=80)] = None,
) -> PullResponse:
    return await pull_service.pull(
        session,
        ctx,
        entities=entities or [],
        since=since,
        limit=limit,
        site_id=site_id,
        device_id=device_id,
    )

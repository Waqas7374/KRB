"""Infrastructure endpoints: liveness, readiness, version.

`/health` answers "is the process alive" and must stay dependency-free — a
health check that touches the database turns a slow query into a restart loop.
`/ready` answers "can this instance serve traffic", and is what the load
balancer and the deploy script use.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.core.config import settings
from app.core.db import check_database
from app.core.types import utcnow

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    app: str
    environment: str


class ReadyComponent(BaseModel):
    name: str
    ready: bool
    detail: str | None = None


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checked_at: str
    components: list[ReadyComponent]


class VersionResponse(BaseModel):
    app: str
    version: str
    git_sha: str
    environment: str
    migration_head: str | None = None


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    return HealthResponse(status="ok", app=settings.app_name, environment=settings.app_env)


@router.get("/ready", response_model=ReadyResponse, summary="Readiness probe")
async def ready(response: Response) -> ReadyResponse:
    components: list[ReadyComponent] = []

    db_ok = await check_database()
    components.append(
        ReadyComponent(
            name="database", ready=db_ok, detail=None if db_ok else "cannot reach PostgreSQL"
        )
    )

    redis_ok, redis_detail = await _check_redis()
    components.append(ReadyComponent(name="redis", ready=redis_ok, detail=redis_detail))

    all_ready = all(component.ready for component in components)
    if not all_ready:
        response.status_code = 503

    return ReadyResponse(
        status="ready" if all_ready else "not_ready",
        checked_at=utcnow().isoformat(),
        components=components,
    )


@router.get("/version", response_model=VersionResponse, summary="Build information")
async def version() -> VersionResponse:
    return VersionResponse(
        app=settings.app_name,
        version=os.getenv("APP_VERSION", "0.1.0"),
        git_sha=os.getenv("GIT_SHA", "dev"),
        environment=settings.app_env,
        migration_head=os.getenv("MIGRATION_HEAD"),
    )


async def _check_redis() -> tuple[bool, str | None]:
    try:
        import redis.asyncio as aioredis

        client: Any = aioredis.from_url(str(settings.redis_url), socket_connect_timeout=2)
        try:
            await client.ping()
            return True, None
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001 — a probe must never raise
        return False, f"cannot reach Redis: {type(exc).__name__}"

"""FastAPI application factory.

Wiring only — no business logic. Middleware order, exception handlers, router
mounting and lifespan live here so the composition of the app is readable in
one place.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.system import router as system_router
from app.core.cache import close_redis
from app.core.config import settings
from app.core.db import dispose_engine
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.models_registry import import_all_models
from app.modules.audit.hooks import install_audit_hooks

log = get_logger("app")

DESCRIPTION = """
ERP for land development: projects, sites, procurement, material deliveries,
inventory, finance and HR.

**Conventions**

* Errors are RFC 9457 `application/problem+json`.
* Collections return `{ items, page, meta }`.
* Mutating requests accept an `Idempotency-Key` header.
* Records outside your permission scope return `404`, not `403`.
"""


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()

    # Must run before the first request: the hook is what makes every audited
    # write produce an audit row, and installing it late would leave a window
    # of unaudited changes.
    install_audit_hooks()

    # Populates Base.metadata, so any code that reflects on the model set sees
    # all of it rather than whatever happened to be imported.
    import_all_models()

    log.info(
        "startup",
        app=settings.app_name,
        environment=settings.app_env,
        debug=settings.debug,
    )
    if settings.sentry_dsn:
        _init_sentry()
    try:
        yield
    finally:
        await close_redis()
        await dispose_engine()
        log.info("shutdown")


def _init_sentry() -> None:
    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        integrations=[FastApiIntegration(), SqlalchemyIntegration(), AsyncioIntegration()],
        send_default_pii=False,
    )
    log.info("sentry.initialised")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
        docs_url=settings.docs_url,
        redoc_url=settings.redoc_url,
        openapi_url=settings.openapi_url,
        root_path=settings.root_path,
        swagger_ui_parameters={"persistAuthorization": True, "docExpansion": "none"},
    )

    # Outermost first.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=settings.cors_allow_credentials,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Idempotency-Key",
                "If-Match",
                "X-Request-ID",
                "X-Company-ID",
                "X-Device-ID",
                "X-App-Version",
            ],
            expose_headers=["X-Request-ID", "ETag", "Retry-After"],
            max_age=600,
        )

    register_exception_handlers(app)

    app.include_router(system_router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    if settings.metrics_enabled:
        _instrument(app)

    return app


def _instrument(app: FastAPI) -> None:
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator(
            should_group_status_codes=False,
            excluded_handlers=["/health", "/ready", "/metrics"],
        ).instrument(app).expose(app, include_in_schema=False, endpoint="/metrics")
    except ImportError:  # pragma: no cover
        log.warning("metrics.unavailable", reason="prometheus instrumentator not installed")


app = create_app()

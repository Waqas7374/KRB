"""HTTP middleware: request context, access logging, security headers.

Ordering matters. Registered outermost-first in `app.main`:

    SecurityHeaders -> RequestContext -> (CORS) -> routes

so that a request id exists before anything can fail, and headers are applied
to error responses too.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import settings
from app.core.context import RequestContext, reset_context, set_context
from app.core.logging import get_logger
from app.core.types import uuid7_str

log = get_logger("http")

# Paths excluded from the access log to keep it readable.
_QUIET_PATHS = frozenset({"/health", "/ready", "/metrics", "/favicon.ico"})


def _client_ip(request: Request) -> str | None:
    """Resolve the client IP, trusting proxy headers only behind our own proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else None


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Establish the request context and emit one access log line per request."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid7_str()
        ctx = RequestContext(
            request_id=request_id,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            device_id=request.headers.get("x-device-id"),
            app_version=request.headers.get("x-app-version"),
        )
        token = set_context(ctx)
        request.state.request_id = request_id

        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            if request.url.path not in _QUIET_PATHS:
                logger = log.bind(
                    method=request.method,
                    path=request.url.path,
                    status=status_code,
                    duration_ms=duration_ms,
                )
                if status_code >= 500:
                    logger.error("request.failed")
                elif status_code >= 400:
                    logger.warning("request.rejected")
                else:
                    logger.info("request.completed")
            reset_context(token)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline response hardening.

    The API serves JSON, not HTML, so the CSP is maximally restrictive; the web
    app is served separately by Cloudflare Pages with its own policy.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Content-Security-Policy": (
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            ),
        }
        if settings.is_production:
            self._headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        # Swagger UI needs to load its own assets; exempt the docs routes.
        is_docs = request.url.path in {"/docs", "/redoc", settings.openapi_url}
        for key, value in self._headers.items():
            if is_docs and key == "Content-Security-Policy":
                continue
            response.headers.setdefault(key, value)
        return response

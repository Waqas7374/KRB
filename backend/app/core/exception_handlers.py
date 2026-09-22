"""Translate exceptions into RFC 9457 problem responses.

Registered once in the app factory. Handlers never leak internals in
production: an unexpected exception is logged in full and returned as a bare
500 with a request id the user can quote.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.context import current_context
from app.core.errors import (
    ERROR_BASE_URI,
    AppError,
    ConflictError,
    RateLimitedError,
    ValidationError,
)
from app.core.logging import get_logger

log = get_logger("errors")

PROBLEM_MEDIA_TYPE = "application/problem+json"


def _problem_response(
    status: int, body: dict[str, Any], headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status, content=body, media_type=PROBLEM_MEDIA_TYPE, headers=headers
    )


def _field_path(location: tuple[Any, ...]) -> str:
    """Turn ('body', 'items', 0, 'quantity') into 'items.0.quantity'."""
    parts = [str(p) for p in location if p not in {"body", "query", "path", "header"}]
    return ".".join(parts) or "body"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        body = exc.to_problem(instance=request.url.path, request_id=current_context().request_id)
        headers = None
        if isinstance(exc, RateLimitedError):
            headers = {"Retry-After": str(exc.retry_after_seconds)}
        if exc.status_code >= 500:
            log.error("app_error", error_type=exc.error_type, detail=exc.detail, exc_info=exc)
        else:
            log.info("app_error", error_type=exc.error_type, detail=exc.detail)
        return _problem_response(exc.status_code, body, headers)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "field": _field_path(err["loc"]),
                "code": err["type"],
                "message": err["msg"],
            }
            for err in exc.errors()
        ]
        problem = ValidationError("One or more fields are invalid.", errors=errors).to_problem(
            instance=request.url.path, request_id=current_context().request_id
        )
        return _problem_response(422, problem)

    @app.exception_handler(PydanticValidationError)
    async def _pydantic_validation(request: Request, exc: PydanticValidationError) -> JSONResponse:
        errors = [
            {"field": _field_path(err["loc"]), "code": err["type"], "message": err["msg"]}
            for err in exc.errors()
        ]
        problem = ValidationError("Response validation failed.", errors=errors).to_problem(
            instance=request.url.path, request_id=current_context().request_id
        )
        log.error("response_validation_failed", errors=errors)
        return _problem_response(422, problem)

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        """Map database constraint violations onto a useful message.

        The constraint naming convention in core.db makes this possible: a
        violation of `uq_vendors_company_id_code` tells us both the table and
        the columns involved.
        """
        constraint = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
        detail = "The operation conflicts with existing data."
        code = "constraint"
        if constraint:
            if constraint.startswith("uq_"):
                detail = "A record with these values already exists."
                code = "duplicate"
            elif constraint.startswith("fk_"):
                detail = "A referenced record does not exist or is still in use."
                code = "foreign_key"
            elif constraint.startswith("ck_"):
                detail = "A data-integrity rule was violated."
                code = "check"

        log.warning("integrity_error", constraint=constraint, error=str(exc.orig))
        problem = ConflictError(
            detail, errors=[{"field": constraint or "", "code": code, "message": detail}]
        ).to_problem(instance=request.url.path, request_id=current_context().request_id)
        return _problem_response(409, problem)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        body = {
            "type": f"{ERROR_BASE_URI}http",
            "title": exc.detail if isinstance(exc.detail, str) else "HTTP error",
            "status": exc.status_code,
            "detail": exc.detail if isinstance(exc.detail, str) else None,
            "instance": request.url.path,
            "request_id": current_context().request_id,
        }
        return _problem_response(exc.status_code, body, dict(exc.headers or {}))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = current_context().request_id
        log.error("unhandled_exception", path=request.url.path, exc_info=exc)
        body = {
            "type": f"{ERROR_BASE_URI}internal",
            "title": "Internal server error",
            "status": 500,
            "detail": (
                f"{type(exc).__name__}: {exc}"
                if not settings.is_production
                else "An unexpected error occurred. Quote the request id when reporting it."
            ),
            "instance": request.url.path,
            "request_id": request_id,
        }
        return _problem_response(500, body)

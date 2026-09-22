"""Structured logging.

Every line carries request_id, user_id and company_id from the request context,
so a support question ("what happened to PO-2026-00184?") is one grep away.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from app.core.config import settings
from app.core.context import current_context


def _add_request_context(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    ctx = current_context()
    if ctx.request_id != "-":
        event_dict["request_id"] = ctx.request_id
    if ctx.user_id:
        event_dict["user_id"] = str(ctx.user_id)
    if ctx.company_id:
        event_dict["company_id"] = str(ctx.company_id)
    if ctx.actor_label:
        event_dict["actor"] = ctx.actor_label
    return event_dict


def _drop_color_message(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    """uvicorn duplicates `event` into `color_message`; drop the noise."""
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging() -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _add_request_context,
        _drop_color_message,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.log_format == "json":
        renderer: Processor = structlog.processors.JSONRenderer()
        shared.append(structlog.processors.format_exc_info)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
        shared.append(structlog.processors.ExceptionPrettyPrinter())

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)

    # Tame the libraries that log an entire paragraph per request.
    for name, level in (
        ("uvicorn", logging.INFO),
        ("uvicorn.error", logging.INFO),
        ("uvicorn.access", logging.WARNING),  # we emit our own access log
        ("sqlalchemy.engine", logging.WARNING),
        ("botocore", logging.WARNING),
        ("celery", logging.INFO),
    ):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
        lg.setLevel(level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name)

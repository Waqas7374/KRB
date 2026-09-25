"""Helpers shared by the procurement routers."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Header

from app.core.access import AccessContext
from app.core.scoping import assert_in_scope

IfMatch = Annotated[int | None, Header(alias="If-Match", description="The version you loaded")]


def may(ctx: AccessContext, permission: str, doc: Any) -> bool:
    """Whether `ctx` holds `permission` in a scope covering `doc`. Used to hand
    the UI capability flags so it never re-derives who may do what."""
    try:
        assert_in_scope(
            ctx,
            permission,
            company_id=doc.company_id,
            project_id=getattr(doc, "project_id", None),
            site_id=getattr(doc, "site_id", None),
        )
    except Exception:  # noqa: BLE001 — any refusal simply means "no"
        return False
    return True

"""Aggregate router for /api/v1.

Module routers are registered here and nowhere else, so the whole API surface
is readable in one file. Adding a module means adding one line.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.access.api import routes as access_routes
from app.modules.identity.api import routes as identity_routes
from app.modules.identity.api import user_routes as identity_user_routes
from app.modules.masterdata.api import routes as masterdata_routes
from app.modules.org.api import routes as org_routes
from app.modules.vendors.api import routes as vendor_routes

api_router = APIRouter()

api_router.include_router(identity_routes.router, prefix="/auth", tags=["auth"])
api_router.include_router(identity_user_routes.router, prefix="/users", tags=["users"])
api_router.include_router(access_routes.router)
api_router.include_router(vendor_routes.router, prefix="/vendors", tags=["vendors"])
api_router.include_router(org_routes.router)
api_router.include_router(masterdata_routes.router)

# Registered as each module lands:

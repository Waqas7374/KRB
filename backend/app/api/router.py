"""Aggregate router for /api/v1.

Module routers are registered here and nowhere else, so the whole API surface
is readable in one file. Adding a module means adding one line.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.access.api import routes as access_routes
from app.modules.approvals.api import routes as approval_routes
from app.modules.deliveries.api import review_routes as delivery_review_routes
from app.modules.deliveries.api import routes as delivery_routes
from app.modules.documents.api import routes as document_routes
from app.modules.finance.api import routes as finance_routes
from app.modules.grn.api import routes as grn_routes
from app.modules.identity.api import routes as identity_routes
from app.modules.identity.api import user_routes as identity_user_routes
from app.modules.inventory.api import routes as inventory_routes
from app.modules.masterdata.api import routes as masterdata_routes
from app.modules.notifications.api import routes as notification_routes
from app.modules.org.api import routes as org_routes
from app.modules.procurement.api import po_routes as purchase_order_routes
from app.modules.procurement.api import routes as procurement_routes
from app.modules.procurement.api import sourcing_routes
from app.modules.rates.api import routes as rates_routes
from app.modules.rules.api import routes as rules_routes
from app.modules.stock.api import routes as stock_routes
from app.modules.sync.api import routes as sync_routes
from app.modules.vendors.api import routes as vendor_routes

api_router = APIRouter()

api_router.include_router(identity_routes.router, prefix="/auth", tags=["auth"])
api_router.include_router(identity_user_routes.router, prefix="/users", tags=["users"])
api_router.include_router(access_routes.router)
api_router.include_router(vendor_routes.router, prefix="/vendors", tags=["vendors"])
api_router.include_router(org_routes.router)
api_router.include_router(masterdata_routes.router)
api_router.include_router(document_routes.router, prefix="/attachments", tags=["attachments"])
api_router.include_router(
    notification_routes.router, prefix="/notifications", tags=["notifications"]
)
api_router.include_router(approval_routes.router, tags=["approvals"])
# Importing the procurement routes also registers the purchase-request
# approval handler with the engine (services/purchase_requests.py).
api_router.include_router(procurement_routes.router)
api_router.include_router(sourcing_routes.rfq_router)
api_router.include_router(sourcing_routes.quotation_router)
api_router.include_router(purchase_order_routes.router)
api_router.include_router(rules_routes.router)
api_router.include_router(rates_routes.router)
# The review router first: `/deliveries/review-queue` must be matched before
# `/deliveries/{delivery_id}` reads it as an id.
api_router.include_router(delivery_review_routes.router)
api_router.include_router(delivery_review_routes.flag_router)
api_router.include_router(delivery_routes.router)
api_router.include_router(grn_routes.delivery_router)
api_router.include_router(grn_routes.router)
api_router.include_router(inventory_routes.router)
api_router.include_router(stock_routes.option_router)
api_router.include_router(stock_routes.issue_router)
api_router.include_router(stock_routes.transfer_router)
api_router.include_router(stock_routes.adjustment_router)
api_router.include_router(sync_routes.router)
api_router.include_router(finance_routes.router)

# Registered as each module lands:

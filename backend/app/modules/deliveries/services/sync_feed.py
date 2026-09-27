"""A phone's own deliveries, as head office sees them (docs/06 §4, §6).

Reference data flows down in `pull`; so must the fate of what the phone sent up.
When a reviewer approves, rejects, or sends an entry back for correction, this is
how the person who captured it finds out — with the reviewer's note, and what they
need to correct it — without opening the web app.

Only the caller's own captures, only the last `WINDOW_DAYS`, and never a price: a
delivery's rate and amount are not part of what a phone may hold.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.sync import FeedRow
from app.core.types import utcnow
from app.modules.deliveries.domain.enums import DeliveryStatus, FlagSeverity, FlagStatus
from app.modules.deliveries.models import Delivery, DeliveryReview

WINDOW_DAYS = 30
_QUANTITY = Decimal("0.0001")  # a fixed shape, whether or not the row was just written


async def changes(
    session: AsyncSession, ctx: AccessContext, *, since: int, limit: int
) -> list[FeedRow]:
    rows = (
        (
            await session.execute(
                select(Delivery)
                .where(
                    Delivery.company_id == ctx.company_id,
                    Delivery.submitted_by_id == ctx.user_id,
                    Delivery.captured_at >= utcnow() - timedelta(days=WINDOW_DAYS),
                    Delivery.server_seq.is_not(None),
                    Delivery.server_seq > since,
                )
                .order_by(Delivery.server_seq)
                .limit(limit)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    latest = await _latest_reviews(session, [d.id for d in rows])
    return [
        FeedRow(
            "my_deliveries",
            d.id,
            int(d.server_seq or 0),
            data=_data(d, latest.get(d.id)),
        )
        for d in rows
    ]


async def _latest_reviews(
    session: AsyncSession, delivery_ids: list[UUID]
) -> dict[UUID, DeliveryReview]:
    if not delivery_ids:
        return {}
    found = (
        (
            await session.execute(
                select(DeliveryReview)
                .where(DeliveryReview.delivery_id.in_(delivery_ids))
                .order_by(DeliveryReview.reviewed_at)
            )
        )
        .scalars()
        .all()
    )
    return {r.delivery_id: r for r in found}  # later rows overwrite earlier ones


def _data(d: Delivery, review: DeliveryReview | None) -> dict[str, Any]:
    return {
        "delivery_number": d.delivery_number,
        "status": d.status,
        # What the phone needs to show the person, and to correct the entry if asked.
        "can_correct": d.status == DeliveryStatus.CORRECTION_REQUESTED.value,
        "flag_count": d.flag_count,
        "open_flags": [
            {"flag_type": f.flag_type, "severity": f.severity, "message": f.message}
            for f in d.flags
            if f.status == FlagStatus.OPEN.value and FlagSeverity(f.severity).needs_review
        ],
        "review": (
            {
                "action": review.action,
                "comments": review.comments,
                "reviewer_name": review.reviewer_name,
                "reviewed_at": review.reviewed_at.isoformat(),
            }
            if review is not None
            else None
        ),
        "rejection_reason": d.rejection_reason,
        "captured_at": d.captured_at.isoformat(),
        # The entry as it was recorded, so a correction can start from it.
        "entry": {
            "site_id": str(d.site_id),
            "vendor_id": str(d.vendor_id),
            "purchase_order_id": str(d.purchase_order_id) if d.purchase_order_id else None,
            "po_item_id": str(d.po_item_id) if d.po_item_id else None,
            "truck_number": d.truck_number,
            "truck_type_id": str(d.truck_type_id) if d.truck_type_id else None,
            "driver_name": d.driver_name,
            "driver_phone": d.driver_phone,
            "challan_number": d.challan_number,
            "challan_date": d.challan_date.isoformat() if d.challan_date else None,
            "captured_at": d.captured_at.isoformat(),
            "latitude": str(d.captured_lat) if d.captured_lat is not None else None,
            "longitude": str(d.captured_lng) if d.captured_lng is not None else None,
            "gps_accuracy_m": str(d.gps_accuracy_m) if d.gps_accuracy_m is not None else None,
            "location_source": d.location_source,
            "remarks": d.remarks,
            "items": [
                {
                    "material_id": str(i.material_id),
                    "unit_id": str(i.unit_id),
                    "quantity": str(i.quantity.quantize(_QUANTITY)),
                    "remarks": i.remarks,
                }
                for i in d.items
            ],
        },
    }

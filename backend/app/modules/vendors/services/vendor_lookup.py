"""Vendor facts other modules need, exposed without the ORM models (module
boundary: tests/unit/test_architecture.py)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.vendors.domain.enums import VendorStatus
from app.modules.vendors.models import Vendor


@dataclass(frozen=True, slots=True)
class VendorInfo:
    id: UUID
    code: str
    name: str
    status: str
    payment_terms_days: int
    currency_code: str

    @property
    def can_receive_orders(self) -> bool:
        return VendorStatus(self.status).can_receive_orders


async def vendors(
    session: AsyncSession, *, company_id: UUID, vendor_ids: set[UUID]
) -> dict[UUID, VendorInfo]:
    if not vendor_ids:
        return {}
    rows = (
        await session.execute(
            select(Vendor).where(
                Vendor.company_id == company_id,
                Vendor.id.in_(list(vendor_ids)),
                Vendor.deleted_at.is_(None),
            )
        )
    ).scalars()
    return {
        v.id: VendorInfo(
            id=v.id,
            code=v.code,
            name=v.trade_name or v.legal_name,
            status=v.status,
            payment_terms_days=v.payment_terms_days,
            currency_code=v.currency_code,
        )
        for v in rows
    }

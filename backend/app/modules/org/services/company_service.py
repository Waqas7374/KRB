"""Company read services.

Other modules reach company data through here rather than importing the ORM
model, so `companies` can gain columns or move behind a cache without every
caller knowing.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.org.models import Company


@dataclass(frozen=True, slots=True)
class CompanyProfile:
    """What other modules need to know about the company.

    Deliberately narrow: currency, fiscal year and timezone drive formatting,
    period boundaries and "today" everywhere else in the system.
    """

    id: UUID
    code: str
    name: str
    legal_name: str | None
    base_currency: str
    fiscal_year_start_month: int
    timezone: str
    locale: str


async def get_profile(session: AsyncSession, company_id: UUID) -> CompanyProfile:
    company = (
        await session.execute(select(Company).where(Company.id == company_id))
    ).scalar_one_or_none()
    if company is None:
        raise NotFoundError("Company", company_id)

    return CompanyProfile(
        id=company.id,
        code=company.code,
        name=company.name,
        legal_name=company.legal_name,
        base_currency=company.base_currency,
        fiscal_year_start_month=company.fiscal_year_start_month,
        timezone=company.timezone,
        locale=company.locale,
    )


async def fiscal_year_start_month(session: AsyncSession, company_id: UUID) -> int:
    """Used by document numbering and period logic."""
    month = await session.scalar(
        select(Company.fiscal_year_start_month).where(Company.id == company_id)
    )
    return int(month or 7)

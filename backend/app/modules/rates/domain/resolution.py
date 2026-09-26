"""Which rate applies (docs/05 §4). Pure: no database.

Inputs are a vendor, a material, a site (and the project it belongs to) and a
date. Among the vendor's approved rates for the material whose period contains
the date, the most specific scope wins: a site rate beats a project rate beats
the company-wide rate. If nothing matches the caller flags RATE_MISSING and
prices the delivery later.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RateView:
    id: UUID
    vendor_id: UUID
    material_id: UUID
    unit_id: UUID
    rate: Decimal
    currency_code: str
    project_id: UUID | None
    site_id: UUID | None
    effective_from: date
    effective_to: date | None
    source: str

    @property
    def specificity(self) -> int:
        """2 = site, 1 = project, 0 = company-wide."""
        if self.site_id is not None:
            return 2
        return 1 if self.project_id is not None else 0

    def in_force(self, at: date) -> bool:
        return self.effective_from <= at and (self.effective_to is None or self.effective_to >= at)

    def applies_to(self, project_id: UUID | None, site_id: UUID | None) -> bool:
        """A site rate needs that site; a project rate needs that project."""
        if self.site_id is not None and self.site_id != site_id:
            return False
        return not (self.project_id is not None and self.project_id != project_id)


def pick(
    rates: Sequence[RateView],
    *,
    at: date,
    project_id: UUID | None,
    site_id: UUID | None,
    unit_id: UUID | None = None,
) -> RateView | None:
    """The winning rate, or None. `unit_id` narrows to one pricing unit; without
    it the vendor's rate in whatever unit they price the material is returned
    and the caller converts."""
    candidates = [
        r
        for r in rates
        if r.in_force(at)
        and r.applies_to(project_id, site_id)
        and (unit_id is None or r.unit_id == unit_id)
    ]
    return max(candidates, key=lambda r: (r.specificity, r.effective_from), default=None)


def change_pct(current: Decimal | None, new: Decimal) -> Decimal | None:
    """How far the new rate moves from the current one, in percent (signed).
    None when there is no current rate to compare with."""
    if current is None or current == 0:
        return None
    return ((new - current) / current * 100).quantize(Decimal("0.01"))

"""What each rule type means: its value shape and where it may be scoped.

A rule's `value` and `scope` are free-form JSON in the database (one table
serves every type), so the shape of each is checked here, on the way in. A
threshold nobody can parse is worse than none: it would fail at the moment a
delivery is being captured on a site.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.rules.domain.enums import RuleType

# The dimensions a rule may be scoped by. A context supplies the same keys.
SCOPE_KEYS = frozenset(
    {
        "project_id",
        "site_id",
        "material_id",
        "material_category_id",
        "vendor_id",
        "truck_type_id",
        "role_id",
        "doc_type",
    }
)

Positive = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
NonNegative = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)]


class _Value(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TonnageMax(_Value):
    max: Positive
    unit: str = "TON"


class GeofenceRadius(_Value):
    radius_m: Annotated[int, Field(gt=0, le=50_000)]


class QtyTolerance(_Value):
    """The greater of a percentage and an absolute amount is allowed."""

    pct: NonNegative = Decimal(0)
    abs: NonNegative = Decimal(0)


class PriceTolerance(_Value):
    pct: NonNegative = Decimal(0)


class Limit(_Value):
    limit: NonNegative
    currency: Annotated[str, Field(min_length=3, max_length=3)] = "PKR"


class ReorderLevel(_Value):
    min_quantity: NonNegative
    reorder_quantity: NonNegative | None = None


class LateSubmission(_Value):
    max_age_days: Annotated[int, Field(ge=0, le=3650)]


class DuplicateWindow(_Value):
    minutes: Annotated[int, Field(gt=0, le=10_080)]


class DailyDeliveryCap(_Value):
    max_deliveries: Annotated[int, Field(gt=0)] | None = None
    max_quantity: Positive | None = None

    @model_validator(mode="after")
    def _one_cap(self) -> DailyDeliveryCap:
        if self.max_deliveries is None and self.max_quantity is None:
            raise ValueError("Set max_deliveries, max_quantity or both")
        return self


class ClockSkewMax(_Value):
    seconds: Annotated[int, Field(gt=0, le=86_400)]


@dataclass(frozen=True, slots=True)
class RuleTypeSpec:
    rule_type: RuleType
    label: str
    description: str
    value_model: type[BaseModel]
    scope_keys: frozenset[str]
    example: dict[str, Any]


_SITE = {"project_id", "site_id"}
_MATERIAL = {"material_id", "material_category_id", "vendor_id", "project_id", "site_id"}


def _spec(
    rule_type: RuleType,
    label: str,
    description: str,
    model: type[BaseModel],
    scope: set[str],
    example: dict[str, Any],
) -> RuleTypeSpec:
    assert scope <= SCOPE_KEYS, f"{rule_type}: unknown scope keys {scope - SCOPE_KEYS}"
    return RuleTypeSpec(rule_type, label, description, model, frozenset(scope), example)


RULE_TYPES: dict[RuleType, RuleTypeSpec] = {
    spec.rule_type: spec
    for spec in (
        _spec(
            RuleType.TONNAGE_MAX,
            "Maximum tonnage",
            "Heaviest load a truck may carry. More specific rules (truck type, material, "
            "site) override broader ones.",
            TonnageMax,
            {"truck_type_id", "material_id", "material_category_id", "vendor_id", *_SITE},
            {"max": "16", "unit": "TON"},
        ),
        _spec(
            RuleType.GEOFENCE_RADIUS,
            "Geofence radius",
            "How far from a site's centre a delivery may be captured. A site's own "
            "radius or boundary overrides this.",
            GeofenceRadius,
            set(_SITE),
            {"radius_m": 500},
        ),
        _spec(
            RuleType.QTY_TOLERANCE,
            "Quantity tolerance",
            "How far received may exceed ordered before a delivery is flagged: the "
            "greater of a percentage and an absolute amount.",
            QtyTolerance,
            set(_MATERIAL),
            {"pct": "2", "abs": "0.5"},
        ),
        _spec(
            RuleType.PRICE_TOLERANCE,
            "Price tolerance",
            "How far an invoiced rate may differ from the order's before a match fails.",
            PriceTolerance,
            set(_MATERIAL),
            {"pct": "0"},
        ),
        _spec(
            RuleType.APPROVAL_LIMIT,
            "Approval limit",
            "The largest document a role may approve. A role with no limit rule is "
            "unlimited for that document type.",
            Limit,
            {"role_id", "doc_type", *_SITE},
            {"limit": "500000", "currency": "PKR"},
        ),
        _spec(
            RuleType.PURCHASE_LIMIT,
            "Purchase limit",
            "The largest purchase a role may commit to without going further up.",
            Limit,
            {"role_id", "doc_type", "material_category_id", *_SITE},
            {"limit": "100000", "currency": "PKR"},
        ),
        _spec(
            RuleType.REORDER_LEVEL,
            "Reorder level",
            "Stock at or below which a material is reported low.",
            ReorderLevel,
            {"material_id", "material_category_id", *_SITE},
            {"min_quantity": "100", "reorder_quantity": "500"},
        ),
        _spec(
            RuleType.LATE_SUBMISSION,
            "Late submission",
            "How old a delivery's capture time may be, on arrival, before it is flagged.",
            LateSubmission,
            set(_SITE),
            {"max_age_days": 2},
        ),
        _spec(
            RuleType.DUPLICATE_WINDOW,
            "Duplicate window",
            "Window within which the same truck, material and site looks like a duplicate.",
            DuplicateWindow,
            set(_SITE),
            {"minutes": 20},
        ),
        _spec(
            RuleType.DAILY_DELIVERY_CAP,
            "Daily delivery cap",
            "Most deliveries (or quantity) per day for a vendor at a site.",
            DailyDeliveryCap,
            {"vendor_id", "material_id", *_SITE},
            {"max_deliveries": 40},
        ),
        _spec(
            RuleType.CLOCK_SKEW_MAX,
            "Clock skew",
            "How far a device's clock may differ from the server's before a delivery is flagged.",
            ClockSkewMax,
            set(_SITE),
            {"seconds": 900},
        ),
    )
}


def validate_value(rule_type: RuleType, value: dict[str, Any]) -> dict[str, Any]:
    """The value as stored: validated, and JSON-safe (decimals become strings)."""
    parsed = RULE_TYPES[rule_type].value_model.model_validate(value)
    return parsed.model_dump(mode="json", exclude_none=True)

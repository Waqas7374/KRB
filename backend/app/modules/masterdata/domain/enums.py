"""Master-data enumerations."""

from __future__ import annotations

from enum import StrEnum


class UnitDimension(StrEnum):
    """What a unit measures.

    Conversions inside a dimension are physics (1 tonne = 1000 kg) and can be
    global. Conversions *across* dimensions depend on the material's density,
    which is why tonne-to-cft rows are material-scoped.
    """

    MASS = "MASS"
    VOLUME = "VOLUME"
    LENGTH = "LENGTH"
    AREA = "AREA"
    COUNT = "COUNT"
    TIME = "TIME"


class ConversionScope(StrEnum):
    """How specific a conversion row is. Most specific wins (§20)."""

    GLOBAL = "GLOBAL"
    MATERIAL = "MATERIAL"
    VENDOR = "VENDOR"
    MATERIAL_VENDOR = "MATERIAL_VENDOR"

    @property
    def specificity(self) -> int:
        return {
            ConversionScope.GLOBAL: 0,
            ConversionScope.VENDOR: 1,
            ConversionScope.MATERIAL: 2,
            ConversionScope.MATERIAL_VENDOR: 3,
        }[self]


class MaterialTracking(StrEnum):
    QUANTITY = "QUANTITY"
    BATCH = "BATCH"
    SERIAL = "SERIAL"


class WarehouseType(StrEnum):
    SITE_STORE = "SITE_STORE"
    CENTRAL = "CENTRAL"
    # Holds stock that has left one site and not yet arrived at another.
    TRANSIT = "TRANSIT"
    # Material rejected at receiving, held pending return to the vendor.
    QUARANTINE = "QUARANTINE"


class CalibrationStatus(StrEnum):
    """A weighbridge reading's lifecycle.

    §20 forbids a hard-coded conversion factor; it does not say where a
    correct one comes from. This is the answer: an admin logs real readings
    (truck weight vs measured volume) rather than typing a number they read
    somewhere, and a person confirms the derived factor before it prices
    anything.
    """

    PENDING = "PENDING"
    # Used to derive a confirmed conversion factor.
    APPLIED = "APPLIED"
    # Excluded as an outlier or a bad reading — kept, never deleted, because a
    # discarded reading is itself evidence of what went wrong.
    DISCARDED = "DISCARDED"

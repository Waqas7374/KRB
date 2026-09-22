"""Shared primitive types and column conventions.

Two things matter here and are enforced everywhere else in the codebase:

1.  Identifiers are UUIDv7 — globally unique so a phone with no connectivity can
    mint one, and time-ordered so B-tree indexes do not fragment the way UUIDv4
    makes them.
2.  Money and quantities are `Decimal` over `NUMERIC`. Floats are banned from
    the financial, inventory and delivery paths; `0.1 + 0.2` has no place in a
    ledger.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Final
from uuid import UUID

import uuid_utils
from sqlalchemy import Date, DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import mapped_column

# --- Identifiers -------------------------------------------------------------


def uuid7() -> UUID:
    """A time-ordered UUID (RFC 9562 v7)."""
    return UUID(str(uuid_utils.uuid7()))


def uuid7_str() -> str:
    return str(uuid_utils.uuid7())


# --- Decimal scales ----------------------------------------------------------

MONEY_PRECISION: Final = 18
MONEY_SCALE: Final = 4
QTY_PRECISION: Final = 18
QTY_SCALE: Final = 4
RATE_PRECISION: Final = 18
RATE_SCALE: Final = 6
PCT_PRECISION: Final = 9
PCT_SCALE: Final = 6
FACTOR_PRECISION: Final = 24
FACTOR_SCALE: Final = 12

ZERO: Final = Decimal("0")
MONEY_QUANT: Final = Decimal("0.0001")
QTY_QUANT: Final = Decimal("0.0001")


def money(value: Decimal | int | str) -> Decimal:
    """Normalise to the money scale, rounding half-up."""
    return Decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def quantity(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)


def to_minor_units(value: Decimal, exponent: int = 2) -> Decimal:
    """Round to a currency's minor unit (PKR: 2 decimal places)."""
    return Decimal(value).quantize(Decimal(1).scaleb(-exponent), rounding=ROUND_HALF_UP)


# --- Time --------------------------------------------------------------------


def utcnow() -> datetime:
    """Timezone-aware UTC now. Naive datetimes are a lint error (ruff DTZ)."""
    return datetime.now(UTC)


def today_utc() -> date:
    return datetime.now(UTC).date()


# --- Column type aliases -----------------------------------------------------
# Used as `Mapped[Money] = mapped_column(...)` so a column's meaning is visible
# at the declaration site rather than buried in Numeric(18, 4).

UUIDpk = Annotated[
    UUID,
    mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7),
]
UUIDfk = Annotated[UUID, mapped_column(PgUUID(as_uuid=True))]

Money = Annotated[Decimal, mapped_column(Numeric(MONEY_PRECISION, MONEY_SCALE))]
Quantity = Annotated[Decimal, mapped_column(Numeric(QTY_PRECISION, QTY_SCALE))]
Rate = Annotated[Decimal, mapped_column(Numeric(RATE_PRECISION, RATE_SCALE))]
Percentage = Annotated[Decimal, mapped_column(Numeric(PCT_PRECISION, PCT_SCALE))]
Factor = Annotated[Decimal, mapped_column(Numeric(FACTOR_PRECISION, FACTOR_SCALE))]

Code = Annotated[str, mapped_column(String(50))]
ShortText = Annotated[str, mapped_column(String(120))]
MediumText = Annotated[str, mapped_column(String(255))]
LongText = Annotated[str, mapped_column(Text)]
Email = Annotated[str, mapped_column(CITEXT)]
Phone = Annotated[str, mapped_column(String(32))]
Status = Annotated[str, mapped_column(String(32))]
CurrencyCode = Annotated[str, mapped_column(String(3))]

Timestamp = Annotated[datetime, mapped_column(DateTime(timezone=True))]
CalendarDate = Annotated[date, mapped_column(Date)]
Json = Annotated[dict, mapped_column(JSONB)]

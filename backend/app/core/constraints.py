"""Reusable table constraints.

Status columns are `text` with a CHECK constraint rather than a PostgreSQL ENUM
type: altering a CHECK is a one-line migration, while adding a value to a PG
enum is transaction-hostile. This helper keeps the constraint and the Python
enum from drifting apart — the SQL is generated from the enum itself.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import CheckConstraint


def enum_check(column: str, enum: type[StrEnum], *, name: str | None = None) -> CheckConstraint:
    """Restrict `column` to the values of `enum`.

    >>> enum_check("status", ProjectStatus)
    CheckConstraint("status IN ('DRAFT', 'ACTIVE', ...)", name="status_valid")
    """
    values = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name or f"{column}_valid")


def positive(column: str, *, name: str | None = None) -> CheckConstraint:
    return CheckConstraint(f"{column} > 0", name=name or f"{column}_positive")


def non_negative(column: str, *, name: str | None = None) -> CheckConstraint:
    return CheckConstraint(f"{column} >= 0", name=name or f"{column}_non_negative")


def percentage(column: str, *, name: str | None = None) -> CheckConstraint:
    return CheckConstraint(f"{column} BETWEEN 0 AND 100", name=name or f"{column}_range")

"""The business-rule store (docs/05 §1): one table for every configurable
threshold, resolved by scope specificity, priority and effective date."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Date, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constraints import enum_check
from app.core.db import CompanyModel, VersionMixin
from app.modules.rules.domain.enums import RuleType


class BusinessRule(CompanyModel, VersionMixin):
    __tablename__ = "business_rules"
    __audited__ = True
    # Company-wide configuration: a project- or site-scoped holder of
    # settings.view still sees the rules (see core/scoping.py).
    __scope_company_wide__ = True

    rule_type: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str | None] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(String(500))
    # {"site_id": "...", "truck_type_id": "..."} — every key set must match the
    # context. An empty object is the company-wide default.
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Optional condition in the approval engine's language, over the context.
    condition: Mapped[Any | None] = mapped_column(JSONB)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("ix_business_rules_type", "company_id", "rule_type", "is_active"),
        enum_check("rule_type", RuleType),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="effective_period_valid"
        ),
    )

"""Organisation enumerations.

Stored as text with CHECK constraints rather than PostgreSQL ENUM types:
adding a value to a PG enum inside a transaction is awkward, while altering a
CHECK constraint is trivial. See docs/02-data-model.md §0.
"""

from __future__ import annotations

from enum import StrEnum


class ProjectStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ON_HOLD = "ON_HOLD"
    COMPLETED = "COMPLETED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"

    @property
    def is_open(self) -> bool:
        """Whether new transactions may be posted against the project."""
        return self in {ProjectStatus.DRAFT, ProjectStatus.ACTIVE, ProjectStatus.ON_HOLD}

    @property
    def is_terminal(self) -> bool:
        return self in {ProjectStatus.CLOSED, ProjectStatus.CANCELLED}


class ProjectType(StrEnum):
    HOUSING_SCHEME = "HOUSING_SCHEME"
    RESIDENTIAL_COMMUNITY = "RESIDENTIAL_COMMUNITY"
    COMMERCIAL = "COMMERCIAL"
    MIXED_USE = "MIXED_USE"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    OTHER = "OTHER"


class PhaseStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    ON_HOLD = "ON_HOLD"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class SiteType(StrEnum):
    """Why a site exists, which drives what may happen there."""

    DEVELOPMENT = "DEVELOPMENT"  # a project's construction area
    CENTRAL_STORE = "CENTRAL_STORE"  # company-level store, no single project
    HEAD_OFFICE = "HEAD_OFFICE"
    PLANT = "PLANT"  # crusher, batching plant — may serve several projects
    DEPOT = "DEPOT"  # equipment / fleet
    OTHER = "OTHER"


# Project phases seeded for every new project (§24 of the brief).
DEFAULT_PROJECT_PHASES: tuple[tuple[str, str], ...] = (
    ("LAND_DEV", "Land Development"),
    ("EARTHWORKS", "Site Development & Earthworks"),
    ("ROADS", "Roads"),
    ("SEWERAGE", "Drainage & Sewerage"),
    ("WATER", "Water Supply"),
    ("ELECTRICAL", "Electrical Infrastructure"),
    ("LANDSCAPING", "Landscaping"),
    ("BUILDINGS", "Buildings & Facilities"),
)

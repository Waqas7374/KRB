"""Single place that imports every ORM model.

Alembic autogenerate compares `Base.metadata` against the live database. A
model that is never imported is absent from the metadata, and autogenerate
happily emits `DROP TABLE` for it. Importing everything from one place makes
that failure impossible, and a test asserts that every `modules/*/models.py`
appears in the list below.
"""

from __future__ import annotations

import importlib
from typing import Final

# Order is irrelevant to SQLAlchemy but kept in dependency order for readability.
MODEL_MODULES: Final[tuple[str, ...]] = (
    # Phase 1 — foundation
    "app.modules.org.models",
    "app.modules.identity.models",
    "app.modules.access.models",
    "app.modules.masterdata.models",
    "app.modules.vendors.models",
    "app.modules.documents.models",
    "app.modules.notifications.models",
    "app.modules.audit.models",
    "app.platform.models",
    # Phase 2 — approvals and procurement
    "app.modules.approvals.models",
    "app.modules.procurement.models",
)


def import_all_models() -> None:
    for module in MODEL_MODULES:
        importlib.import_module(module)

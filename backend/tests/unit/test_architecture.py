"""Architecture conventions enforced by inspection.

`import-linter` handles the layer contracts. Two rules it cannot express are
checked here by walking the AST:

1. A module may use its *own* internals but must reach another module only
   through that module's `services` or `domain.enums`.
2. Every `modules/*/models.py` must be listed in `models_registry`, because an
   unlisted model is invisible to Alembic autogenerate — which then proposes
   dropping its table.

There is also a set of catalogue-consistency checks: the permission catalogue,
the seeded roles and the document-numbering registry all have to agree with the
code that references them.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[2] / "app"

# What one module may import from another.
PUBLIC_SUBPACKAGES = ("services", "schemas", "domain.enums")


def _module_files() -> list[pathlib.Path]:
    return sorted(p for p in (APP / "modules").rglob("*.py") if p.name != "__init__.py")


def _imported_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def _owning_module(path: pathlib.Path) -> str:
    """`app/modules/vendors/services/x.py` -> `vendors`."""
    parts = path.relative_to(APP / "modules").parts
    return parts[0]


class TestModuleBoundaries:
    def test_modules_reach_each_other_only_through_public_surfaces(self) -> None:
        """A module must not import another module's models or repositories.

        Where that coupling would be circular — inventory needs GL posting, GL
        needs nothing from inventory — the dependency is inverted with a domain
        event instead.
        """
        violations: list[str] = []

        for path in _module_files():
            owner = _owning_module(path)
            tree = ast.parse(path.read_text(encoding="utf-8"))

            for imported in _imported_names(tree):
                if not imported.startswith("app.modules."):
                    continue
                parts = imported.split(".")
                target = parts[2]
                if target == owner:
                    continue  # own internals are fine

                remainder = ".".join(parts[3:])
                if not remainder:
                    continue
                if any(
                    remainder == allowed or remainder.startswith(f"{allowed}.")
                    for allowed in PUBLIC_SUBPACKAGES
                ):
                    continue

                violations.append(
                    f"{path.relative_to(APP.parent)} imports {imported} "
                    f"(reach module '{target}' through {' / '.join(PUBLIC_SUBPACKAGES)})"
                )

        assert violations == [], "Module boundary violations:\n  " + "\n  ".join(violations)

    def test_domain_packages_import_no_infrastructure(self) -> None:
        """The domain layer stays pure so its rules are testable in isolation."""
        forbidden = ("sqlalchemy", "fastapi", "redis", "celery", "boto3", "httpx")
        violations: list[str] = []

        for path in _module_files():
            if "domain" not in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for imported in _imported_names(tree):
                root = imported.split(".")[0]
                if root in forbidden:
                    violations.append(f"{path.relative_to(APP.parent)} imports {imported}")

        assert violations == [], "Domain purity violations:\n  " + "\n  ".join(violations)


class TestModelRegistry:
    def test_every_models_module_is_registered(self) -> None:
        """An unregistered model is invisible to Alembic, which then proposes
        dropping its table. This test is the reason that cannot happen."""
        from app.models_registry import MODEL_MODULES

        on_disk = {
            "app."
            + str(path.relative_to(APP.parent / "app")).replace("\\", "/")[:-3].replace("/", ".")
            for path in APP.rglob("models.py")
        }
        registered = set(MODEL_MODULES)

        missing = on_disk - registered
        assert missing == set(), (
            "These model modules exist but are not in models_registry.MODEL_MODULES: "
            f"{sorted(missing)}"
        )

    def test_registered_modules_all_import(self) -> None:
        from app.models_registry import import_all_models

        import_all_models()

    def test_every_table_has_a_primary_key_and_timestamps(self) -> None:
        from app.core.db import Base
        from app.models_registry import import_all_models

        import_all_models()

        # Tables that legitimately differ: the audit log is append-only and has
        # no updated_at, because a row is never updated.
        exempt_from_timestamps = {"audit_logs", "role_permissions"}

        for name, table in Base.metadata.tables.items():
            assert table.primary_key.columns, f"{name} has no primary key"
            if name in exempt_from_timestamps:
                continue
            assert "created_at" in table.columns, f"{name} has no created_at"

    def test_money_and_quantity_columns_are_never_floats(self) -> None:
        """A float in a financial or inventory column is a defect, not a choice."""
        from sqlalchemy import Float

        from app.core.db import Base
        from app.models_registry import import_all_models

        import_all_models()

        offenders = [
            f"{table_name}.{column.name}"
            for table_name, table in Base.metadata.tables.items()
            for column in table.columns
            if isinstance(column.type, Float)
        ]
        assert offenders == [], f"Float columns found: {offenders}"


class TestPermissionCatalogue:
    def test_codes_are_unique(self) -> None:
        from app.modules.access.domain.permissions import ALL_PERMISSIONS

        codes = [p.code for p in ALL_PERMISSIONS]
        assert len(codes) == len(set(codes))

    def test_codes_are_well_formed(self) -> None:
        from app.modules.access.domain.permissions import ALL_PERMISSIONS

        for permission in ALL_PERMISSIONS:
            assert "." in permission.code, permission.code
            assert permission.code == permission.code.lower(), permission.code
            assert permission.description, permission.code

    def test_role_definitions_resolve(self) -> None:
        """A typo in a role's permission list fails here rather than at seed."""
        from app.modules.access.domain.roles import validate_catalogue

        validate_catalogue()

    def test_every_permission_belongs_to_at_least_one_role(self) -> None:
        """An unreachable permission is either dead code or a missing grant."""
        from app.modules.access.domain.permissions import PERMISSION_CODES
        from app.modules.access.domain.roles import STANDARD_ROLES

        granted: set[str] = set()
        for role in STANDARD_ROLES:
            granted |= role.resolve()

        orphans = PERMISSION_CODES - granted
        assert orphans == set(), f"Permissions no role can hold: {sorted(orphans)}"

    @pytest.mark.parametrize(
        "code", ["projects.view", "deliveries.create", "finance.gl.post", "audit.view"]
    )
    def test_require_known_accepts_real_codes(self, code: str) -> None:
        from app.modules.access.domain.permissions import require_known

        assert require_known(code) == code

    def test_require_known_rejects_a_typo(self) -> None:
        from app.modules.access.domain.permissions import require_known

        with pytest.raises(KeyError):
            require_known("deliveries.aprove")


class TestDocumentNumbering:
    def test_every_document_type_has_a_prefix(self) -> None:
        from app.platform.numbering import PREFIXES, DocumentType

        declared = {
            value
            for name, value in vars(DocumentType).items()
            if not name.startswith("_") and isinstance(value, str)
        }
        assert declared == set(PREFIXES), (
            f"DocumentType and PREFIXES disagree: {sorted(declared ^ set(PREFIXES))}"
        )

    def test_prefixes_are_unique(self) -> None:
        from app.platform.numbering import PREFIXES

        prefixes = [prefix for prefix, _resets in PREFIXES.values()]
        duplicates = {p for p in prefixes if prefixes.count(p) > 1}
        assert duplicates == set(), f"Duplicate number prefixes: {sorted(duplicates)}"

    def test_fiscal_year_follows_the_july_start(self) -> None:
        """Pakistan runs 1 July to 30 June (decision Q1)."""
        import datetime as dt

        from app.platform.numbering import fiscal_year_of, format_fiscal_year

        assert fiscal_year_of(dt.date(2026, 8, 12), 7) == 2026
        assert fiscal_year_of(dt.date(2026, 5, 12), 7) == 2025
        assert fiscal_year_of(dt.date(2026, 7, 1), 7) == 2026
        assert fiscal_year_of(dt.date(2026, 6, 30), 7) == 2025
        assert format_fiscal_year(2026, 7) == "2026-27"
        assert format_fiscal_year(2026, 1) == "2026"

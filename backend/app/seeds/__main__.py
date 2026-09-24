"""Seed runner: `python -m app.seeds`.

Idempotent. Safe to run against a database that already has data — every
seeder upserts by natural key and leaves administrator edits alone.

    python -m app.seeds              # everything
    python -m app.seeds --only access
    python -m app.seeds --list
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.db import SessionFactory, dispose_engine
from app.core.logging import configure_logging, get_logger
from app.models_registry import import_all_models
from app.modules.audit.hooks import install_audit_hooks
from app.seeds import foundation, masterdata
from app.seeds.registry import SeedResult

log = get_logger("seed")

# Order matters: later groups reference earlier ones.
GROUPS: tuple[str, ...] = (
    "company",
    "access",
    "org",
    "users",
    "units",
    "materials",
    "warehouses",
    "vendors",
)


async def run(groups: tuple[str, ...]) -> list[SeedResult]:
    results: list[SeedResult] = []

    async with SessionFactory() as session:
        company, company_result = await foundation.seed_company(session)
        if "company" in groups:
            results.append(company_result)

        if "access" in groups:
            results.append(await foundation.seed_permissions(session))
            results.append(await foundation.seed_roles(session, company))

        if "org" in groups:
            results.append(await foundation.seed_org(session, company))

        if "users" in groups:
            results.append(await foundation.seed_users(session, company))

        if "units" in groups:
            results.append(await masterdata.seed_units(session, company))

        if "materials" in groups:
            results.append(await masterdata.seed_materials(session, company))

        if "warehouses" in groups:
            results.append(await masterdata.seed_warehouses(session, company))

        if "vendors" in groups:
            results.append(await masterdata.seed_vendors(session, company))

        await session.commit()

    return results


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.seeds")
    parser.add_argument(
        "--only",
        nargs="+",
        choices=GROUPS,
        help="Seed only these groups (dependencies are still resolved).",
    )
    parser.add_argument("--list", action="store_true", help="List the groups and exit.")
    parser.add_argument(
        "--reset-dev-passwords",
        action="store_true",
        help="Reset seeded accounts to the dev password with no forced change "
        "(end-to-end test setup). Refused in production.",
    )
    args = parser.parse_args()

    if args.list:
        for group in GROUPS:
            sys.stdout.write(f"{group}\n")
        return 0

    configure_logging()
    import_all_models()
    install_audit_hooks()

    if args.reset_dev_passwords:
        return _reset_dev_passwords()

    groups = tuple(args.only) if args.only else GROUPS

    # Seeds run as the system actor, so their audit rows are labelled as
    # machine-originated rather than attributed to a person.
    from app.core.context import system_context

    async def _main() -> list[SeedResult]:
        with system_context("seed"):
            try:
                return await run(groups)
            finally:
                await dispose_engine()

    results = asyncio.run(_main())

    sys.stdout.write("\nSeed summary\n" + "-" * 58 + "\n")
    for result in results:
        sys.stdout.write(f"{result}\n")
    total_created = sum(r.created for r in results)
    total_updated = sum(r.updated for r in results)
    sys.stdout.write("-" * 58 + f"\ncreated {total_created}, updated {total_updated}\n")
    if total_created or total_updated:
        sys.stdout.write(
            "\nDemo sign-in: admin@krb.example / "
            f"{foundation.DEV_PASSWORD}  (password change required)\n"
        )
    return 0


def _reset_dev_passwords() -> int:
    from app.core.config import settings
    from app.core.context import system_context

    if settings.is_production:
        sys.stderr.write("Refusing to reset passwords in production.\n")
        return 2

    async def _main() -> SeedResult:
        with system_context("seed"):
            try:
                async with SessionFactory() as session:
                    company, _ = await foundation.seed_company(session)
                    result = await foundation.reset_dev_passwords(session, company)
                    await session.commit()
                    return result
            finally:
                await dispose_engine()

    sys.stdout.write(f"{asyncio.run(_main())}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

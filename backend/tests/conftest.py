"""Shared test fixtures.

The environment is forced to `test` **before** anything imports `app.core.config`,
because Settings is instantiated at import time. In particular the database URL
is rewritten to a dedicated `*_test` database, so running the suite can never
truncate the development data sitting in the same container.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# --- Environment must be set before app.core.config is imported --------------

os.environ["APP_ENV"] = "test"
os.environ["DEBUG"] = "false"
os.environ["LOG_LEVEL"] = os.environ.get("TEST_LOG_LEVEL", "WARNING")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-validation-ok")

# Argon2 at production parameters makes the auth tests take minutes.
os.environ["ARGON2_TIME_COST"] = "1"
os.environ["ARGON2_MEMORY_COST"] = "8192"
os.environ["ARGON2_PARALLELISM"] = "1"


def _to_test_database(url: str | None, fallback: str) -> str:
    """Point a database URL at a sibling `_test` database."""
    if not url:
        return fallback
    if re.search(r"_test(\?|$)", url):
        return url
    return re.sub(r"/([^/?]+)(\?|$)", r"/\1_test\2", url)


os.environ["DATABASE_URL"] = _to_test_database(
    os.environ.get("DATABASE_URL"),
    "postgresql+asyncpg://krb:krb_dev_password@localhost:5432/krb_erp_test",
)
os.environ["DATABASE_URL_SYNC"] = _to_test_database(
    os.environ.get("DATABASE_URL_SYNC"),
    "postgresql+psycopg://krb:krb_dev_password@localhost:5432/krb_erp_test",
)
# Keep Redis test state off the development databases (0, 1, 2).
os.environ["REDIS_URL"] = re.sub(
    r"/\d+$", "/9", os.environ.get("REDIS_URL", "redis://localhost:6379/9")
)


def pytest_configure(config: pytest.Config) -> None:
    """Use the application's logging setup in tests.

    Without it structlog falls back to its defaults, which render exception
    tracebacks with every frame's local variables through rich. A single
    unhandled error inside a request (locals: sessions, ORM objects, the app)
    then takes many minutes to print, and the suite looks hung.
    """
    from app.core.logging import configure_logging

    configure_logging()


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


# -----------------------------------------------------------------------------
# Integration harness
# -----------------------------------------------------------------------------
# Defined at the root so the integration and authorisation suites share one
# copy. The heavy fixtures are lazy: a unit test that never asks for `db` pays
# nothing for them.


@pytest.fixture(scope="session")
def migrated_database(database_available: bool) -> Iterator[None]:
    """Bring the test database up to the migration head, once per session."""
    if not database_available:
        pytest.skip("PostgreSQL is not reachable — run `make up` first")

    from alembic import command
    from alembic.config import Config

    from app.core.config import settings

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", settings.database_url_sync)
    # Start from a known state: a half-applied schema from an earlier failed
    # run would produce confusing failures far from the cause.
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield


@pytest.fixture(scope="session")
def seeded(migrated_database: None) -> Iterator[None]:
    """Load the demo dataset once per session."""
    import asyncio

    from app.core.context import system_context
    from app.core.db import SessionFactory, dispose_engine
    from app.models_registry import import_all_models
    from app.modules.audit.hooks import install_audit_hooks
    from app.seeds import approvals, finance, foundation, masterdata, rules

    import_all_models()
    install_audit_hooks()

    async def _seed() -> None:
        with system_context("test-seed"):
            async with SessionFactory() as session:
                company, _ = await foundation.seed_company(session)
                await foundation.seed_permissions(session)
                await foundation.seed_roles(session, company)
                await foundation.seed_org(session, company)
                await foundation.seed_users(session, company)
                await masterdata.seed_units(session, company)
                await masterdata.seed_materials(session, company)
                await masterdata.seed_warehouses(session, company)
                await masterdata.seed_vendors(session, company)
                await finance.seed_accounts(session, company)
                await finance.seed_periods(session, company)
                await finance.seed_posting_rules(session, company)
                await finance.seed_tax_codes(session, company)
                await finance.seed_bank_accounts(session, company)
                await approvals.seed_workflows(session, company)
                await rules.seed_rules(session, company)
                await session.commit()
        # Dispose inside this loop: asyncpg connections belong to the loop that
        # opened them, and leaving them for a later loop to close raises
        # "Event loop is closed" from somewhere unhelpful.
        await dispose_engine()

    # asyncio.run() owns and closes its loop, unlike new_event_loop().
    asyncio.run(_seed())
    yield


@pytest_asyncio.fixture
async def engine(seeded: None) -> AsyncIterator[AsyncEngine]:
    from app.core.config import settings

    eng = create_async_engine(
        settings.database_url, poolclass=None, connect_args={"statement_cache_size": 0}
    )
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """An open connection with an outer transaction that is always rolled back."""
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            yield conn
        finally:
            await transaction.rollback()


@pytest_asyncio.fixture
async def db(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """A session joined to the test's transaction.

    `join_transaction_mode="create_savepoint"` lets application code call
    `commit()` — which services legitimately do — without escaping the test's
    rollback.
    """

    factory = sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )
    async with factory() as session:  # type: ignore[call-arg]
        yield session


@pytest_asyncio.fixture
async def api(db: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An HTTP client whose requests run in the test's transaction."""
    from app.api.deps import get_session, get_uow
    from app.core.db import UnitOfWork
    from app.main import create_app

    application = create_app()

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield db

    async def _override_uow() -> AsyncIterator[UnitOfWork]:
        uow = UnitOfWork(db)
        yield uow
        # The route finished without raising, so mirror what the real
        # dependency does and flush the work into the test's savepoint.
        await db.flush()

    application.dependency_overrides[get_session] = _override_session
    application.dependency_overrides[get_uow] = _override_uow

    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as client:
        yield client


@pytest_asyncio.fixture
async def login(api: AsyncClient) -> Any:
    """Return a helper that signs a seeded user in and yields auth headers."""

    async def _login(email: str, password: str = "KrbDev!Passw0rd") -> dict[str, str]:
        response = await api.post("/auth/login", json={"identifier": email, "password": password})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _login


# -----------------------------------------------------------------------------
# Unit-test fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def app() -> Iterator[object]:
    from app.main import create_app

    yield create_app()


@pytest.fixture
async def client(app: object) -> AsyncIterator[object]:
    import httpx

    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(scope="session")
def database_available() -> bool:
    """True when the test database is reachable, creating it if necessary."""
    import sqlalchemy
    from sqlalchemy.exc import SQLAlchemyError

    from app.core.config import settings

    url = sqlalchemy.engine.make_url(settings.database_url_sync)
    try:
        admin = sqlalchemy.create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            exists = conn.execute(
                sqlalchemy.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            ).scalar()
            if not exists:
                conn.execute(sqlalchemy.text(f'CREATE DATABASE "{url.database}"'))
        admin.dispose()

        engine = sqlalchemy.create_engine(url)
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        engine.dispose()
        return True
    except SQLAlchemyError:
        return False


@pytest.fixture(autouse=True)
def _skip_integration_without_db(request: pytest.FixtureRequest, database_available: bool) -> None:
    if request.node.get_closest_marker("integration") and not database_available:
        pytest.skip("PostgreSQL is not reachable — run `make up` first")

"""Infrastructure endpoints and the error contract."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from app.core.errors import BusinessRuleError, NotFoundError


class QuantityPayload(BaseModel):
    """Declared at module level on purpose.

    This module uses `from __future__ import annotations`, so FastAPI resolves
    route annotations from module globals. A model defined inside a test
    function is invisible to that lookup and the route silently misbehaves.
    """

    quantity: int


class TestHealth:
    async def test_health_is_ok_without_touching_infrastructure(
        self, client: httpx.AsyncClient
    ) -> None:
        """A health check that hits the database turns a slow query into a restart loop."""
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_every_response_carries_a_request_id(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/health")
        assert response.headers["X-Request-ID"]

    async def test_supplied_request_id_is_echoed(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/health", headers={"X-Request-ID": "trace-me-123"})
        assert response.headers["X-Request-ID"] == "trace-me-123"

    async def test_security_headers_are_present(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/health")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "Content-Security-Policy" in response.headers


class TestVersion:
    async def test_reports_build_information(self, client: httpx.AsyncClient) -> None:
        body = (await client.get("/version")).json()
        assert body["version"]
        assert body["environment"] == "test"


class TestErrorContract:
    """Errors must be RFC 9457 problem documents, from every raise site."""

    @pytest.fixture
    def app_with_failing_routes(self, app: FastAPI) -> FastAPI:
        @app.get("/_test/not-found")
        async def _not_found() -> None:
            raise NotFoundError("Vendor", "VEN-0042")

        @app.get("/_test/business-rule")
        async def _business_rule() -> None:
            raise BusinessRuleError(
                "TONNAGE_MAX", "22.0t exceeds the 16.0t maximum for a 10-wheeler"
            )

        @app.get("/_test/boom")
        async def _boom() -> None:
            raise RuntimeError("something unexpected")

        return app

    async def test_not_found_problem_document(self, app_with_failing_routes: FastAPI) -> None:
        transport = httpx.ASGITransport(app=app_with_failing_routes, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/_test/not-found")

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["title"] == "Not found"
        assert body["detail"] == "Vendor VEN-0042 not found"
        assert body["instance"] == "/_test/not-found"
        assert body["request_id"]

    async def test_business_rule_violation_names_the_rule(
        self, app_with_failing_routes: FastAPI
    ) -> None:
        transport = httpx.ASGITransport(app=app_with_failing_routes, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/_test/business-rule")

        assert response.status_code == 422
        body = response.json()
        assert body["rule"] == "TONNAGE_MAX"
        assert "16.0t" in body["detail"]

    async def test_unhandled_exception_is_a_500_problem(
        self, app_with_failing_routes: FastAPI
    ) -> None:
        transport = httpx.ASGITransport(app=app_with_failing_routes, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/_test/boom")

        assert response.status_code == 500
        body = response.json()
        assert body["status"] == 500
        assert body["request_id"]

    async def test_validation_error_reports_field_paths(self, app: FastAPI) -> None:
        @app.post("/_test/validate")
        async def _validate(payload: QuantityPayload) -> dict[str, int]:
            return {"ok": payload.quantity}

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post("/_test/validate", json={"quantity": "not-a-number"})

        assert response.status_code == 422
        errors = response.json()["errors"]
        assert errors[0]["field"] == "quantity"
        assert errors[0]["code"] == "int_parsing"

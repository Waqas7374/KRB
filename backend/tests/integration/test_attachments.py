"""Attachments end to end: presign, a real upload to MinIO, confirm, list,
download and soft-remove.

`app.platform.storage.S3ObjectStore` is exercised for real here rather than
mocked — the whole point of `confirm_upload` is that it trusts what MinIO
reports back over `HEAD`, never the client's claim, and a mock can't catch a
regression in that trust boundary. The pytest process runs inside the `api`
container, on the same Docker network as `minio:9000`, so the presigned URL
that `/attachments/presign` returns is reachable directly.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _sign_for_the_internal_address(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """These tests upload from *inside* the API container, so they need URLs signed for the
    Docker-network address. In the running stack, browsers and phones are given the public one
    (`STORAGE_PUBLIC_BASE_URL`); that is covered in tests/unit/test_storage_urls.py."""
    from app.core.config import settings
    from app.platform import storage

    monkeypatch.setattr(settings, "storage_public_base_url", None)
    monkeypatch.setattr(storage, "_store", None)  # rebuilt with the internal signer
    yield
    monkeypatch.setattr(storage, "_store", None)  # and rebuilt again for whoever comes next


PROCUREMENT = "procurement@krb.example"
SITE_STAFF = "staff.gvh1@krb.example"

# A minimal but genuine PDF: real magic bytes, so looks_like_declared_type()
# has something real to check rather than an empty body.
_PDF_BYTES = b"%PDF-1.4\ntest attachment content\n"


async def _seeded_vendor_id(api: AsyncClient, headers: dict[str, str]) -> str:
    body = (await api.get("/vendors", headers=headers, params={"limit": 1})).json()
    return str(body["items"][0]["id"])


class TestPresignValidation:
    async def test_rejects_an_unsupported_content_type(self, api: AsyncClient, login: Any) -> None:
        headers = await login(PROCUREMENT)
        vendor_id = await _seeded_vendor_id(api, headers)

        response = await api.post(
            "/attachments/presign",
            headers=headers,
            json={
                "entity_type": "vendor",
                "entity_id": vendor_id,
                "file_name": "notes.txt",
                "content_type": "text/plain",
                "size_bytes": 25,
            },
        )
        assert response.status_code == 422
        assert response.json()["rule"] == "unsupported_content_type"

    async def test_rejects_a_file_over_the_size_limit(self, api: AsyncClient, login: Any) -> None:
        """The schema's own `size_bytes` cap (26_214_400, matching
        `settings.max_upload_bytes`'s 25 MiB default) rejects an oversized
        request before the service's `file_too_large` business rule ever
        gets a chance to — that rule only fires when an operator configures
        a smaller `max_upload_bytes` than the schema allows. Both are
        legitimate 422s; this only proves the boundary is actually enforced
        somewhere, not which layer does it under default settings."""
        headers = await login(PROCUREMENT)
        vendor_id = await _seeded_vendor_id(api, headers)

        response = await api.post(
            "/attachments/presign",
            headers=headers,
            json={
                "entity_type": "vendor",
                "entity_id": vendor_id,
                "file_name": "huge.pdf",
                "content_type": "application/pdf",
                "size_bytes": 26_214_401,
            },
        )
        assert response.status_code == 422

    async def test_confirming_before_anything_was_actually_uploaded_fails(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login(PROCUREMENT)
        vendor_id = await _seeded_vendor_id(api, headers)

        presigned = (
            await api.post(
                "/attachments/presign",
                headers=headers,
                json={
                    "entity_type": "vendor",
                    "entity_id": vendor_id,
                    "file_name": "never-uploaded.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": len(_PDF_BYTES),
                },
            )
        ).json()

        response = await api.post(
            f"/attachments/{presigned['attachment_id']}/confirm", headers=headers
        )
        assert response.status_code == 422
        assert response.json()["rule"] == "upload_not_found"


class TestAttachmentLifecycle:
    async def test_presign_upload_confirm_list_download_and_remove(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login(PROCUREMENT)
        vendor_id = await _seeded_vendor_id(api, headers)

        presigned = (
            await api.post(
                "/attachments/presign",
                headers=headers,
                json={
                    "entity_type": "vendor",
                    "entity_id": vendor_id,
                    "file_name": "lifecycle-test.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": len(_PDF_BYTES),
                },
            )
        ).json()
        attachment_id = presigned["attachment_id"]

        # The upload itself is a real object PUT straight to MinIO — the API
        # never sees these bytes, matching how a browser would do it.
        async with httpx.AsyncClient() as raw:
            put = await raw.put(
                presigned["upload_url"],
                content=_PDF_BYTES,
                headers={"Content-Type": "application/pdf"},
            )
        assert put.status_code == 200

        confirmed = (
            await api.post(f"/attachments/{attachment_id}/confirm", headers=headers)
        ).json()
        assert confirmed["is_confirmed"] is True
        # Real size/type from MinIO's HEAD response, not the client's claim.
        assert confirmed["size_bytes"] == len(_PDF_BYTES)
        assert confirmed["content_type"] == "application/pdf"

        listing = (
            await api.get(
                "/attachments",
                headers=headers,
                params={"entity_type": "vendor", "entity_id": vendor_id},
            )
        ).json()
        assert any(a["id"] == attachment_id for a in listing)

        download = (await api.get(f"/attachments/{attachment_id}/download", headers=headers)).json()
        async with httpx.AsyncClient() as raw:
            fetched = await raw.get(download["url"])
        assert fetched.status_code == 200
        assert fetched.content == _PDF_BYTES

        removed = await api.post(
            f"/attachments/{attachment_id}/remove",
            headers=headers,
            json={"reason": "test cleanup"},
        )
        assert removed.status_code == 200

        after = (
            await api.get(
                "/attachments",
                headers=headers,
                params={"entity_type": "vendor", "entity_id": vendor_id},
            )
        ).json()
        assert all(a["id"] != attachment_id for a in after)


class TestAttachmentPermissions:
    async def test_site_staff_cannot_delete_an_attachment(
        self, api: AsyncClient, login: Any
    ) -> None:
        """SITE_STAFF can upload delivery evidence but never delete it — the
        role definition deliberately withholds `attachments.delete` so a
        photo, once attached, cannot be removed by the person who took it."""
        pm_headers = await login(PROCUREMENT)
        vendor_id = await _seeded_vendor_id(api, pm_headers)

        presigned = (
            await api.post(
                "/attachments/presign",
                headers=pm_headers,
                json={
                    "entity_type": "vendor",
                    "entity_id": vendor_id,
                    "file_name": "for-permission-test.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": len(_PDF_BYTES),
                },
            )
        ).json()

        staff_headers = await login(SITE_STAFF)
        response = await api.post(
            f"/attachments/{presigned['attachment_id']}/remove",
            headers=staff_headers,
            json={"reason": "should not be allowed"},
        )
        assert response.status_code == 403
        assert response.json()["required_permission"] == "attachments.delete"

    async def test_procurement_manager_can_delete_after_the_rbac_fix(
        self, api: AsyncClient, login: Any
    ) -> None:
        """Regression test for the gap this session found live: no role had
        `attachments.delete` at all, so a wrongly-attached document could
        never be corrected by anyone but SUPER_ADMIN."""
        headers = await login(PROCUREMENT)
        vendor_id = await _seeded_vendor_id(api, headers)

        presigned = (
            await api.post(
                "/attachments/presign",
                headers=headers,
                json={
                    "entity_type": "vendor",
                    "entity_id": vendor_id,
                    "file_name": "correctable.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": len(_PDF_BYTES),
                },
            )
        ).json()

        response = await api.post(
            f"/attachments/{presigned['attachment_id']}/remove",
            headers=headers,
            json={"reason": "wrong document attached"},
        )
        assert response.status_code == 200

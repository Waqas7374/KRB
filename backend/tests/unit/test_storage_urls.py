"""Presigned URLs are for people, not for the server: the address in them must be one
a browser or a phone can reach, and because the signature covers the host they are signed
for it rather than rewritten afterwards."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.platform.storage import S3ObjectStore


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "storage_access_key", "test-access")
    monkeypatch.setattr(settings, "storage_secret_key", "test-secret")
    monkeypatch.setattr(settings, "storage_endpoint_url", "http://minio:9000")


def test_urls_use_the_public_address_when_there_is_one(
    credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "storage_public_base_url", "http://localhost:9000")
    store = S3ObjectStore()
    up = store.presign_upload("c/e/1/x.png", content_type="image/png").upload_url
    down = store.presign_download("c/e/1/x.png", filename="x.png").url
    for url in (up, down):
        assert url.startswith("http://localhost:9000/")
        assert "minio" not in url
        assert "X-Amz-Signature=" in url  # signed for that host, not rewritten


def test_without_a_public_address_the_endpoint_is_used(
    credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "storage_public_base_url", None)
    url = S3ObjectStore().presign_upload("c/e/1/x.png", content_type="image/png").upload_url
    assert url.startswith("http://minio:9000/")


def test_an_empty_public_address_counts_as_none(
    credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The setting arrives as an empty string when the variable is declared but unset.
    monkeypatch.setattr(settings, "storage_public_base_url", "")
    url = S3ObjectStore().presign_upload("c/e/1/x.png", content_type="image/png").upload_url
    assert url.startswith("http://minio:9000/")

"""Object storage.

One `ObjectStore` port with one S3-protocol implementation, because R2, B2, S3
and MinIO all speak the same API. Swapping providers in production is a
configuration change (`STORAGE_ENDPOINT_URL`), never a code change (§28).

Uploads go direct from the client to storage via a presigned PUT, never
through the API process: a truck-delivery photo from a site with poor
connectivity should not tie up a request worker for the duration of the
upload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import boto3
from botocore.client import Config as BotoConfig

from app.core.config import settings
from app.core.types import uuid7_str

# Content types accepted end to end. Enforced both when issuing a presigned
# URL (so the browser's PUT is rejected by S3 itself if it lies about the
# type) and again when confirming (§35: validate uploads by content, not by
# trusting the client).
DEFAULT_ALLOWED_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)

# The first bytes of a file, matched against its declared content type.
# Deliberately narrow: this catches "renamed .exe as .jpg", not every
# possible forgery, which is what a magic-byte check can realistically do.
_MAGIC_BYTES: dict[str, tuple[bytes, ...]] = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
    "application/pdf": (b"%PDF-",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (b"PK\x03\x04",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (b"PK\x03\x04",),
}


def looks_like_declared_type(content_type: str, head: bytes) -> bool:
    """Best-effort check that a file's first bytes match its declared type.

    An unknown content type passes (nothing to check against); a known one
    must match at least one of its signatures.
    """
    signatures = _MAGIC_BYTES.get(content_type)
    if signatures is None:
        return True
    return any(head.startswith(sig) for sig in signatures)


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    storage_key: str
    upload_url: str
    method: str = "PUT"
    expires_in: int = 900


@dataclass(frozen=True, slots=True)
class PresignedDownload:
    url: str
    expires_in: int = 900


class ObjectStore(Protocol):
    """The port. `S3ObjectStore` is the only implementation, but the seam
    exists so storage can move without touching a single caller (§28)."""

    def build_key(
        self, *, company_id: str, entity_type: str, entity_id: str, filename: str
    ) -> str: ...
    def presign_upload(self, key: str, *, content_type: str) -> PresignedUpload: ...
    def presign_download(self, key: str, *, filename: str | None = None) -> PresignedDownload: ...
    def delete(self, key: str) -> None: ...
    def head(self, key: str) -> dict[str, object] | None: ...


class S3ObjectStore:
    """Works against MinIO locally and R2/B2/S3 in production — same API,
    only the endpoint URL and credentials change."""

    def __init__(self) -> None:
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.storage_endpoint_url,
            aws_access_key_id=settings.storage_access_key,
            aws_secret_access_key=settings.storage_secret_key,
            region_name=settings.storage_region,
            config=BotoConfig(signature_version="s3v4"),
        )
        self._bucket = settings.storage_bucket

    def build_key(self, *, company_id: str, entity_type: str, entity_id: str, filename: str) -> str:
        """`{company}/{entity_type}/{entity_id}/{uuid}-{safe filename}`.

        The uuid prefix guarantees uniqueness even if the same file is
        uploaded twice, and keeps the original filename readable for a human
        scanning the bucket.
        """
        safe_name = "".join(c for c in filename if c.isalnum() or c in "._- ")[:120] or "file"
        return f"{company_id}/{entity_type}/{entity_id}/{uuid7_str()}-{safe_name}"

    def presign_upload(self, key: str, *, content_type: str) -> PresignedUpload:
        url = self._client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=settings.storage_presign_ttl_seconds,
        )
        return PresignedUpload(
            storage_key=key, upload_url=url, expires_in=settings.storage_presign_ttl_seconds
        )

    def presign_download(self, key: str, *, filename: str | None = None) -> PresignedDownload:
        params: dict[str, str] = {"Bucket": self._bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        url = self._client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=settings.storage_presign_ttl_seconds
        )
        return PresignedDownload(url=url, expires_in=settings.storage_presign_ttl_seconds)

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def head(self, key: str) -> dict[str, object] | None:
        """Confirm an object exists (and get its real size/type) after an
        upload — never trust the client's claim about what it uploaded."""
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except self._client.exceptions.ClientError:
            return None
        return {
            "size_bytes": response.get("ContentLength"),
            "content_type": response.get("ContentType"),
            "etag": response.get("ETag", "").strip('"'),
        }


_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    global _store
    if _store is None:
        _store = S3ObjectStore()
    return _store

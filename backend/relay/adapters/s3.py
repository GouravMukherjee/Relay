"""AWS S3 storage adapter.

Provides presigned PUT URL generation, object download, and deletion for
raw document files stored in S3.

Required creds: ``aws_access_key_id``, ``aws_secret_access_key``,
``aws_region``, ``s3_bucket``.

Uses the synchronous ``boto3`` client (the sanctioned dependency) wrapped in
``asyncio.to_thread`` so the public surface stays fully async without blocking
the event loop.
"""
from __future__ import annotations

import asyncio
import logging

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

from relay.config import settings

logger = logging.getLogger(__name__)

# Presigned URL validity in seconds (1 hour).
_PRESIGNED_TTL = 3600


class S3Storage:
    """AWS S3 adapter for raw document file storage.

    Presigned PUT URLs are generated for client-side direct uploads (the raw
    file never passes through the API server for uploads).  The ingestion
    worker later reads the object back via ``get_object``.

    Required settings
    -----------------
    aws_access_key_id     : str — AWS access key ID
    aws_secret_access_key : str — AWS secret access key (never logged)
    aws_region            : str — AWS region (default: ``us-east-1``)
    s3_bucket             : str — Bucket name for raw documents
    """

    def __init__(self) -> None:
        if not settings.aws_access_key_id:
            raise RuntimeError(
                "S3Storage requires AWS_ACCESS_KEY_ID to be set in the environment."
            )
        if not settings.aws_secret_access_key:
            raise RuntimeError(
                "S3Storage requires AWS_SECRET_ACCESS_KEY to be set in the environment."
            )
        if not settings.s3_bucket:
            raise RuntimeError(
                "S3Storage requires S3_BUCKET to be set in the environment."
            )

        self._bucket = settings.s3_bucket
        self._region = settings.aws_region

        boto_config = Config(
            signature_version="s3v4",
            region_name=self._region,
        )
        # A single synchronous client is reused across calls; boto3 clients are
        # thread-safe for these operations.
        self._client = boto3.client(
            "s3",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=self._region,
            config=boto_config,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def presigned_put_url(self, key: str, content_type: str) -> str:
        """Return a presigned PUT URL valid for ``_PRESIGNED_TTL`` seconds.

        The caller (frontend or API layer) uploads the raw file directly to
        S3 using this URL — the file never passes through the application
        server.

        Args:
            key:          S3 object key (e.g. ``"uploads/doc_abc123.pdf"``).
            content_type: MIME type of the file being uploaded.

        Returns:
            HTTPS presigned PUT URL string.
        """

        def _sign() -> str:
            return self._client.generate_presigned_url(
                ClientMethod="put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "ContentType": content_type,
                },
                ExpiresIn=_PRESIGNED_TTL,
                HttpMethod="PUT",
            )

        url: str = await asyncio.to_thread(_sign)
        logger.info(
            "s3_presigned_put_ok",
            extra={"key": key, "content_type": content_type},
        )
        return url

    async def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """Upload *data* to *key* (server-side direct upload).

        Used by the document upload route when the file is small enough to
        buffer in memory. For large client-side uploads use presigned_put_url.
        """
        def _put() -> None:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )

        await asyncio.to_thread(_put)
        logger.info("s3_put_object_ok", extra={"key": key, "size_bytes": len(data)})

    async def get_object(self, key: str) -> bytes:
        """Download and return the raw bytes for *key*.

        Used by the ingestion worker to fetch the uploaded document for parsing.

        Args:
            key: S3 object key.

        Returns:
            Raw file bytes.
        """

        def _get() -> bytes:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()

        body: bytes = await asyncio.to_thread(_get)
        logger.info(
            "s3_get_object_ok",
            extra={"key": key, "size_bytes": len(body)},
        )
        return body

    async def delete(self, key: str) -> None:
        """Delete the S3 object at *key*.

        Idempotent — deleting a non-existent key is not an error.

        Args:
            key: S3 object key.
        """
        await asyncio.to_thread(
            self._client.delete_object, Bucket=self._bucket, Key=key
        )
        logger.info("s3_delete_ok", extra={"key": key})

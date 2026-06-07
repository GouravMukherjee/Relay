"""Async ingestion pipeline: raw bytes → parsed text → chunks → embeddings → index.

:func:`ingest_document` is the single entry point.  It is:

* **Idempotent** — existing chunks for the document are deleted before re-ingestion
  so re-running is safe and produces exactly one set of chunks.
* **Privileged** — runs under :func:`relay.db.base.privileged_session` (no RLS) and
  scopes all DB writes to the document's ``organization_id`` explicitly.
* **Audited** — emits an ``audit_log`` row on successful completion.

Dependency injection: the three sponsor-backed services (parser, embeddings,
retrieval) are passed in by the caller (arq worker or seed script) so tests can
inject mocks without touching env creds.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, update

from relay.db.base import privileged_session
from relay.db.models import AuditLog, Chunk, Document
from relay.ids import new_id
from relay.ingestion.chunking import chunk_text
from relay.interfaces.embeddings import Embeddings
from relay.interfaces.parser import DocumentParser
from relay.interfaces.retrieval import RetrievalService, RetrievedChunk
from relay.logging import get_logger, log_latency

logger = get_logger(__name__)


async def ingest_document(
    document_id: str,
    *,
    parser: DocumentParser,
    embeddings: Embeddings,
    retrieval: RetrievalService,
    raw_bytes: bytes | None = None,
    s3_client: Any | None = None,
) -> None:
    """Full ingestion pipeline for a single document.

    The document row must already exist in the DB with ``status="processing"``
    (created by the upload route).

    Steps:
    1. Load raw bytes from S3 (unless *raw_bytes* is provided directly — e.g. in tests).
    2. Parse bytes via ``DocumentParser.parse``.
    3. Chunk the resulting text via :func:`~relay.ingestion.chunking.chunk_text`.
    4. Embed chunks in a single batch via ``Embeddings.embed``.
    5. Delete any previously-indexed chunks for this document (idempotency).
    6. Write ``Chunk`` rows to Postgres.
    7. Call ``RetrievalService.index`` to push chunks to Moss + pgvector.
    8. Update ``Document.status = "ready"`` and ``chunk_count``.
    9. Write an ``AuditLog`` entry.

    On any error the document is marked ``status="failed"`` and the exception
    is re-raised so the arq worker can record it.

    Args:
        document_id:  Prefixed doc ID (e.g. ``"doc_abc123"``).
        parser:       :class:`~relay.interfaces.parser.DocumentParser` implementation.
        embeddings:   :class:`~relay.interfaces.embeddings.Embeddings` implementation.
        retrieval:    :class:`~relay.interfaces.retrieval.RetrievalService` implementation.
        raw_bytes:    Raw file bytes.  If ``None``, the document's ``s3_key`` is used to
                      fetch them via *s3_client*.
        s3_client:    Optional S3 storage adapter (required when *raw_bytes* is ``None``).
    """
    start_ts = time.monotonic()
    logger.info(
        "ingestion started",
        extra={"document_id": document_id, "stage": "start"},
    )

    try:
        # ------------------------------------------------------------------
        # Step 1: Fetch raw bytes from S3 if not provided directly.
        # ------------------------------------------------------------------
        async with privileged_session() as session:
            result = await session.execute(
                select(Document).where(Document.id == document_id)
            )
            doc: Document | None = result.scalar_one_or_none()

        if doc is None:
            raise ValueError(f"Document not found: {document_id}")

        org_id: str = str(doc.organization_id)
        title: str = doc.title
        source_type: str = doc.source_type
        s3_key: str | None = doc.s3_key

        if raw_bytes is None:
            if s3_client is None:
                raise ValueError(
                    "Either raw_bytes or s3_client must be provided when s3_key is set."
                )
            if s3_key is None:
                raise ValueError(
                    f"Document {document_id} has no s3_key and no raw_bytes were provided."
                )
            raw_bytes = await s3_client.get_object(s3_key)
            logger.info(
                "fetched raw bytes from S3",
                extra={
                    "document_id": document_id,
                    "s3_key": s3_key,
                    "bytes": len(raw_bytes),
                },
            )

        # ------------------------------------------------------------------
        # Step 2: Parse
        # ------------------------------------------------------------------
        _content_type = _source_type_to_mime(source_type)
        t_parse_start = time.monotonic()
        parsed = await parser.parse(raw_bytes, _content_type, filename=None)
        t_parse_ms = (time.monotonic() - t_parse_start) * 1000
        log_latency(logger, "parse", latency_ms=t_parse_ms, document_id=document_id)

        if not parsed.text.strip():
            raise ValueError(
                f"Parser returned empty text for document {document_id}. "
                "Check the file format and parser configuration."
            )

        # ------------------------------------------------------------------
        # Step 3: Chunk
        # ------------------------------------------------------------------
        texts = chunk_text(parsed.text)
        if not texts:
            raise ValueError(
                f"No chunks produced for document {document_id}. "
                "The parsed text may be too short."
            )
        logger.info(
            "chunked document",
            extra={"document_id": document_id, "chunk_count": len(texts)},
        )

        # ------------------------------------------------------------------
        # Step 4: Embed (single batch) — OPTIONAL.
        # Moss embeds server-side, so embeddings are only needed to populate the
        # pgvector fallback. If the embeddings service is unavailable/misconfigured,
        # proceed with NULL vectors and rely on Moss (the live retrieval path).
        # ------------------------------------------------------------------
        vectors: list[list[float] | None]
        t_embed_start = time.monotonic()
        try:
            embedded = await embeddings.embed(texts)
            if len(embedded) != len(texts):
                raise ValueError(
                    f"Embeddings returned {len(embedded)} vectors for {len(texts)} chunks."
                )
            vectors = list(embedded)
        except Exception as exc:  # noqa: BLE001 — embeddings optional in the Moss-first path
            logger.warning(
                "embeddings unavailable; indexing to Moss without pgvector vectors",
                extra={"document_id": document_id, "error": str(exc)},
            )
            vectors = [None] * len(texts)
        t_embed_ms = (time.monotonic() - t_embed_start) * 1000
        log_latency(
            logger,
            "embed",
            latency_ms=t_embed_ms,
            document_id=document_id,
            batch_size=len(texts),
        )

        # ------------------------------------------------------------------
        # Step 5: Delete existing chunks (idempotency)
        # ------------------------------------------------------------------
        async with privileged_session() as session:
            await session.execute(
                delete(Chunk).where(Chunk.document_id == document_id)
            )
        # Also remove from the retrieval index (Moss + pgvector).
        await retrieval.delete(document_id)
        logger.info(
            "deleted existing chunks",
            extra={"document_id": document_id},
        )

        # ------------------------------------------------------------------
        # Step 6: Write new Chunk rows to Postgres
        # ------------------------------------------------------------------
        chunk_rows: list[Chunk] = []
        retrieved_chunks: list[RetrievedChunk] = []

        for ordinal, (text, vector) in enumerate(zip(texts, vectors)):
            chunk_id = new_id("chk")
            snippet = text[:200].rstrip() + ("…" if len(text) > 200 else "")
            chunk_rows.append(
                Chunk(
                    id=chunk_id,
                    document_id=document_id,
                    organization_id=org_id,  # type: ignore[arg-type]
                    ordinal=ordinal,
                    text=text,
                    embedding=vector,
                    moss_ref=None,  # filled in after retrieval.index returns
                    metadata_={"ordinal": ordinal, "title": title},
                )
            )
            retrieved_chunks.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    title=title,
                    text=text,
                    snippet=snippet,
                    score=0.0,
                    moss_ref=None,
                )
            )

        async with privileged_session() as session:
            session.add_all(chunk_rows)

        logger.info(
            "wrote chunk rows",
            extra={"document_id": document_id, "chunk_count": len(chunk_rows)},
        )

        # ------------------------------------------------------------------
        # Step 7: Index into Moss + pgvector via RetrievalService.index
        # ------------------------------------------------------------------
        t_index_start = time.monotonic()
        if hasattr(retrieval, "index_with_org"):
            await retrieval.index_with_org(retrieved_chunks, org_id=org_id)  # type: ignore[attr-defined]
        else:
            await retrieval.index(retrieved_chunks)
        t_index_ms = (time.monotonic() - t_index_start) * 1000
        log_latency(
            logger,
            "retrieval_index",
            latency_ms=t_index_ms,
            document_id=document_id,
            chunk_count=len(retrieved_chunks),
        )

        # ------------------------------------------------------------------
        # Step 8: Update Document status → ready
        # ------------------------------------------------------------------
        async with privileged_session() as session:
            await session.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(status="ready", chunk_count=len(chunk_rows))
            )

        # ------------------------------------------------------------------
        # Step 9: Audit log
        # ------------------------------------------------------------------
        total_ms = (time.monotonic() - start_ts) * 1000
        async with privileged_session() as session:
            session.add(
                AuditLog(
                    organization_id=org_id,  # type: ignore[arg-type]
                    actor_id=None,
                    action="document.ingest",
                    target_type="document",
                    target_id=document_id,
                    metadata_={
                        "chunk_count": len(chunk_rows),
                        "latency_ms": round(total_ms, 1),
                    },
                )
            )

        log_latency(
            logger,
            "ingest_document",
            latency_ms=total_ms,
            document_id=document_id,
            chunk_count=len(chunk_rows),
            status="ready",
        )

    except Exception as exc:
        logger.error(
            "ingestion failed",
            extra={"document_id": document_id, "error": str(exc)},
            exc_info=True,
        )
        # Mark document as failed so the UI can surface the error.
        try:
            async with privileged_session() as session:
                await session.execute(
                    update(Document)
                    .where(Document.id == document_id)
                    .values(status="failed")
                )
        except Exception as inner:
            logger.error(
                "failed to set document status=failed",
                extra={"document_id": document_id, "error": str(inner)},
            )
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MIME_MAP: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "txt": "text/plain",
    "md": "text/markdown",
    "html": "text/html",
    "htm": "text/html",
    "csv": "text/csv",
    "json": "application/json",
    "xml": "application/xml",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _source_type_to_mime(source_type: str) -> str:
    """Map a ``Document.source_type`` value to a MIME type string."""
    source_type = source_type.lower().lstrip(".")
    return _MIME_MAP.get(source_type, "application/octet-stream")

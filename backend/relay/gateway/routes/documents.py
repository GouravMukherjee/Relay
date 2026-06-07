"""Document ingestion and management routes.

Endpoints
---------
POST   /documents            Upload & enqueue ingestion of a document (multipart)
GET    /documents            List all documents for the authenticated org
GET    /documents/{id}       Get a single document record
DELETE /documents/{id}       Remove a document + its chunks from Moss + Postgres

All paths are mounted under /api/v1 by create_app().
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from relay.auth.deps import current_claims
from relay.auth.jwt import Claims
from relay.db.base import get_session
from relay.db.models import AuditLog, Document
from relay.ids import new_id
from relay.schemas.documents import (
    DocumentListResponse,
    DocumentRecord,
    DocumentUploadResponse,
    document_to_schema,
)

logger = logging.getLogger("relay.gateway.routes.documents")

router = APIRouter(tags=["documents"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "application/octet-stream",  # browsers sometimes send this for unknown types
}

MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB


def _infer_source_type(filename: str | None, content_type: str | None) -> str:
    """Return a normalised source_type string from the uploaded file's name/ct."""
    name = (filename or "").lower()
    if name.endswith(".pdf") or content_type == "application/pdf":
        return "pdf"
    if name.endswith(".docx"):
        return "docx"
    return "txt"


async def _get_document_or_404(
    document_id: str,
    session: AsyncSession,
) -> Document:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "session_not_found", "message": f"Document {document_id!r} not found"}},
        )
    return doc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/documents",
    status_code=202,
    response_model=DocumentUploadResponse,
    summary="Upload and enqueue a document for ingestion",
)
async def upload_document(
    file: UploadFile,
    title: Annotated[Optional[str], Form()] = None,
    tags: Annotated[Optional[List[str]], Form()] = None,
    claims: Claims = Depends(current_claims),
    session: AsyncSession = Depends(get_session),
) -> DocumentUploadResponse:
    """Multipart upload.  Writes a Document row (status=processing), stores the
    raw bytes in S3 (presigned or direct), enqueues the ingestion worker, and
    emits an audit_log row.  Returns 202 immediately.
    """
    # ---- content-type validation ----
    ct = file.content_type or ""
    filename = file.filename or ""
    if ct and ct not in ALLOWED_CONTENT_TYPES and not filename.endswith(
        (".pdf", ".docx", ".txt")
    ):
        raise HTTPException(
            status_code=415,
            detail={"error": {"code": "document_unsupported", "message": f"Unsupported file type: {ct!r}"}},
        )

    raw = await file.read()

    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"error": {"code": "document_too_large", "message": "File exceeds 50 MB limit"}},
        )

    doc_id = new_id("doc")
    source_type = _infer_source_type(filename, ct)
    resolved_title = title or (filename or doc_id)
    resolved_tags = tags or []
    org_id = claims.org_id

    # ---- S3 storage ----
    s3_key: str | None = None
    try:
        from relay.adapters.s3 import S3Storage

        storage = S3Storage()
        candidate_key = f"orgs/{org_id}/documents/{doc_id}/{filename or 'upload'}"
        await storage.put_object(candidate_key, raw, content_type=ct or "application/octet-stream")
        s3_key = candidate_key  # only set after a confirmed successful upload
    except Exception as exc:  # noqa: BLE001
        # S3 not configured or upload failed — s3_key stays None so the
        # ingestion worker won't try to fetch a non-existent object.
        logger.warning("S3 upload skipped: %s", exc)

    # ---- DB row ----
    doc = Document(
        id=doc_id,
        organization_id=org_id,
        title=resolved_title,
        source_type=source_type,
        status="processing",
        tags=resolved_tags,
        chunk_count=0,
        s3_key=s3_key,
    )
    session.add(doc)

    # ---- Audit log ----
    session.add(
        AuditLog(
            organization_id=org_id,
            actor_id=claims.user_id,
            action="document.upload",
            target_type="document",
            target_id=doc_id,
            metadata_={"title": resolved_title, "source_type": source_type},
        )
    )

    # commit handled by get_session

    # ---- Enqueue ingestion worker ----
    try:
        from relay.ingestion.worker import enqueue_ingest

        await enqueue_ingest(doc_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to enqueue ingestion for %s: %s", doc_id, exc)

    logger.info(
        "document.upload doc_id=%s org_id=%s title=%r size=%d",
        doc_id,
        org_id,
        resolved_title,
        len(raw),
    )
    return DocumentUploadResponse(document_id=doc_id, status="processing")


@router.get(
    "/documents",
    response_model=DocumentListResponse,
    summary="List documents for the authenticated org",
)
async def list_documents(
    claims: Claims = Depends(current_claims),
    session: AsyncSession = Depends(get_session),
) -> DocumentListResponse:
    result = await session.execute(select(Document).order_by(Document.created_at.desc()))
    docs = result.scalars().all()
    return DocumentListResponse(documents=[document_to_schema(d) for d in docs])


@router.get(
    "/documents/{document_id}",
    response_model=DocumentRecord,
    summary="Get a single document record",
)
async def get_document(
    document_id: str,
    claims: Claims = Depends(current_claims),
    session: AsyncSession = Depends(get_session),
) -> DocumentRecord:
    doc = await _get_document_or_404(document_id, session)
    return document_to_schema(doc)


@router.delete(
    "/documents/{document_id}",
    status_code=204,
    summary="Delete a document and all its chunks",
)
async def delete_document(
    document_id: str,
    claims: Claims = Depends(current_claims),
    session: AsyncSession = Depends(get_session),
) -> None:
    doc = await _get_document_or_404(document_id, session)
    org_id = claims.org_id

    # ---- Remove from Moss + pgvector ----
    try:
        from relay.retrieval.service import CompositeRetrievalService

        retrieval = CompositeRetrievalService.from_settings()
        await retrieval.delete(document_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Retrieval delete failed for %s: %s", document_id, exc)

    # ---- Remove S3 object ----
    if doc.s3_key:
        try:
            from relay.adapters.s3 import S3Storage

            await S3Storage().delete(doc.s3_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("S3 delete failed for %s: %s", doc.s3_key, exc)

    # ---- Audit log ----
    session.add(
        AuditLog(
            organization_id=org_id,
            actor_id=claims.user_id,
            action="document.delete",
            target_type="document",
            target_id=document_id,
            metadata_={"title": doc.title},
        )
    )

    await session.delete(doc)
    # commit handled by get_session
    logger.info("document.delete doc_id=%s org_id=%s", document_id, org_id)

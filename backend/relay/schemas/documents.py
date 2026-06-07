"""Document schemas — mirror types.ts DocumentRecord exactly."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


# ── Shared object shape ───────────────────────────────────────────────────────

class DocumentRecord(BaseModel):
    document_id: str       # doc_…
    title: str
    status: Literal["processing", "ready", "failed"]
    chunk_count: int
    created_at: str        # ISO-8601 UTC


# ── Request / Response models ─────────────────────────────────────────────────

class DocumentUploadResponse(BaseModel):
    """202 response for POST /documents."""
    document_id: str
    status: Literal["processing"] = "processing"


class DocumentListResponse(BaseModel):
    """GET /documents response."""
    documents: list[DocumentRecord]


# ── Mapper from DB model ──────────────────────────────────────────────────────

def document_to_schema(doc: object) -> DocumentRecord:
    """Map a relay.db.models.Document ORM instance to DocumentRecord.

    The DB model uses ``id`` as its PK; the external schema exposes it
    as ``document_id``.
    """
    return DocumentRecord(
        document_id=doc.id,  # type: ignore[attr-defined]
        title=doc.title,  # type: ignore[attr-defined]
        status=doc.status,  # type: ignore[attr-defined]
        chunk_count=doc.chunk_count or 0,  # type: ignore[attr-defined]
        created_at=doc.created_at.isoformat() if doc.created_at else "",  # type: ignore[attr-defined]
    )

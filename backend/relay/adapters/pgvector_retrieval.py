"""pgvector retrieval adapter.

Implements ``RetrievalService`` using cosine-similarity search over
``chunks.embedding`` via SQLAlchemy + pgvector.

This is the *fallback* path — used when Moss is unavailable.

Required creds: ``database_url`` (always present; has a default).
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from relay.config import settings
from relay.db.base import async_session_maker
from relay.db.models import Chunk, Document
from relay.interfaces.retrieval import RetrievalResult, RetrievedChunk, RetrievalService

logger = logging.getLogger(__name__)

# Maximum characters used for the display snippet.
_SNIPPET_LEN = 200


class PgVectorRetrieval(RetrievalService):
    """Retrieval service backed by pgvector cosine similarity.

    Uses the shared async SQLAlchemy session factory from ``relay.db.base``.
    This is the fallback path — Moss failures route here automatically via
    ``CompositeRetrievalService``.

    No additional credentials are required beyond the database connection
    (``DATABASE_URL``), which always has a default value and is required by
    the broader application.
    """

    def __init__(self) -> None:
        # database_url always has a default; no hard requirement check needed.
        # Actual DB connectivity is validated at engine creation, not here.
        pass

    # ------------------------------------------------------------------
    # RetrievalService interface
    # ------------------------------------------------------------------

    async def query(
        self,
        org_id: str,
        text: str,
        k: int = 5,
    ) -> RetrievalResult:
        """Return top-*k* chunks ranked by cosine similarity to *text*.

        The embedding for *text* is obtained by re-using the application's
        ``Embeddings`` interface.  To avoid a circular dependency the adapter
        accepts an optional injected embeddings provider; if none is supplied
        it imports lazily from the adapters package.

        Note: this method performs an inline embed call so callers should
        prefer Moss for latency-critical paths.
        """
        # Lazy import to avoid circular deps at module level.
        from relay.adapters.embeddings_factory import get_embeddings  # noqa: PLC0415

        embeddings_svc = get_embeddings()
        query_vec = (await embeddings_svc.embed([text]))[0]

        # pgvector cosine distance operator: <=> (lower = more similar).
        # We convert distance to similarity: similarity = 1 - distance.
        async with async_session_maker() as session:
            # Use privileged session (worker scope) since fallback path may be
            # called outside of a request context. RLS is enforced here by
            # filtering on organization_id explicitly.
            org_uuid = UUID(org_id)
            stmt = (
                select(
                    Chunk,
                    Document.title,
                    (
                        1.0
                        - Chunk.embedding.cosine_distance(query_vec)  # type: ignore[attr-defined]
                    ).label("score"),
                )
                .join(Document, Chunk.document_id == Document.id)
                .where(Chunk.organization_id == org_uuid)
                .order_by(sa_text("score DESC"))
                .limit(k)
            )
            result = await session.execute(stmt)
            rows = result.all()

        chunks: list[RetrievedChunk] = []
        for row in rows:
            chunk: Chunk = row[0]
            doc_title: str = row[1] or ""
            score: float = float(row[2] or 0.0)
            chunk_text: str = chunk.text or ""
            chunks.append(
                RetrievedChunk(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    title=doc_title,
                    text=chunk_text,
                    snippet=chunk_text[:_SNIPPET_LEN],
                    score=score,
                    moss_ref=chunk.moss_ref,
                )
            )

        logger.info(
            "pgvector_query_ok",
            extra={"org_id": org_id, "k": k, "hits": len(chunks)},
        )
        return RetrievalResult(chunks=chunks, backend="pgvector")

    async def index(self, chunks: list[RetrievedChunk]) -> None:
        """Upsert *chunks* into the pgvector store.

        Idempotent: existing rows with the same ``chunk_id`` are replaced via
        an ON CONFLICT DO UPDATE in the underlying DB (handled by the ingestion
        pipeline which deletes existing chunks first before bulk-inserting).

        This method just logs; the actual writes are done by the ingestion
        pipeline (``relay.ingestion.pipeline``) which has full access to the
        ORM session with proper RLS context.  The ``CompositeRetrievalService``
        calls this to signal that pgvector indexing should happen — the
        pipeline writes the rows.
        """
        # The ingestion pipeline writes Chunk rows directly; nothing to do here
        # from the adapter's perspective.  The ivfflat index is maintained
        # automatically by PostgreSQL.
        logger.debug(
            "pgvector_index_noop",
            extra={"chunk_count": len(chunks)},
        )

    async def delete(self, document_id: str) -> None:
        """Delete all Chunk rows for *document_id* from the pgvector store."""
        async with async_session_maker() as session:
            await session.execute(
                delete(Chunk).where(Chunk.document_id == document_id)
            )
            await session.commit()
        logger.info("pgvector_delete_ok", extra={"document_id": document_id})

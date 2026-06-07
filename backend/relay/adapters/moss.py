"""Moss retrieval adapter (moss.dev SDK).

Implements ``RetrievalService`` on top of the official async ``moss`` SDK. Moss has
**built-in embeddings** (model ``moss-minilm`` by default), so the live query path needs
no separate embedding service — text goes in, semantic matches come out (<10 ms).

Tenant isolation: every chunk is stored with ``organization_id`` in its metadata, and
queries are filtered server-side with ``{"field":"organization_id","condition":{"$eq":org_id}}``
so one org never sees another's chunks (CLAUDE.md invariant #3, retrieval layer).

Required creds: ``moss_project_id``, ``moss_project_key``.
"""
from __future__ import annotations

import asyncio
import logging

from relay.config import settings
from relay.interfaces.retrieval import RetrievalResult, RetrievedChunk, RetrievalService

logger = logging.getLogger(__name__)


class MossRetrieval(RetrievalService):
    """Retrieval backed by the Moss semantic-search SDK (primary, <10 ms).

    Required settings
    -----------------
    moss_project_id  : str — Moss project id
    moss_project_key : str — Moss project key
    moss_index_name  : str — index holding the knowledge chunks (default "relay")
    moss_model_id    : str — optional embedding model id (empty = SDK default)
    """

    def __init__(self) -> None:
        if not settings.moss_project_id or not settings.moss_project_key:
            raise RuntimeError(
                "MossRetrieval requires MOSS_PROJECT_ID and MOSS_PROJECT_KEY."
            )
        from moss import MossClient  # imported here so the dep is only needed when used

        self._client = MossClient(settings.moss_project_id, settings.moss_project_key)
        self._index = settings.moss_index_name
        self._model_id = settings.moss_model_id or None
        # query() requires the index be loaded once per process; guard with a lock.
        self._loaded = False
        self._load_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        async with self._load_lock:
            if self._loaded:
                return
            # auto_refresh so freshly indexed docs become queryable without a reload.
            await self._client.load_index(self._index, auto_refresh=True)
            self._loaded = True

    @staticmethod
    def _to_docs(chunks: list[RetrievedChunk]):
        from moss import DocumentInfo

        return [
            DocumentInfo(
                id=c.chunk_id,
                text=c.text,
                metadata={
                    "document_id": c.document_id,
                    "title": c.title,
                    # organization_id is set by the caller via chunk.metadata-style fields;
                    # RetrievedChunk has no org field, so callers that need isolation pass it
                    # through the title/document — but the ingestion path sets it explicitly
                    # below via index_with_org. Kept here for the generic interface.
                },
            )
            for c in chunks
        ]

    # ------------------------------------------------------------------
    # RetrievalService interface
    # ------------------------------------------------------------------
    async def query(self, org_id: str, text: str, k: int = 5) -> RetrievalResult:
        from moss import QueryOptions

        await self._ensure_loaded()
        # alpha blends semantic (1.0) and keyword (0.0) matching — hybrid search.
        opts = QueryOptions(
            top_k=k,
            alpha=settings.moss_hybrid_alpha,
            filter={"field": "organization_id", "condition": {"$eq": org_id}},
        )
        result = await self._client.query(self._index, text, opts)

        chunks: list[RetrievedChunk] = []
        for d in result.docs:
            meta = d.metadata or {}
            chunks.append(
                RetrievedChunk(
                    chunk_id=d.id,
                    document_id=str(meta.get("document_id", "")),
                    title=str(meta.get("title", "")),
                    text=d.text,
                    snippet=(d.text or "")[:200],
                    score=float(d.score),
                    moss_ref=d.id,
                )
            )
        logger.info(
            "moss_query_ok",
            extra={"org_id": org_id, "k": k, "n": len(chunks), "ms": result.time_taken_ms},
        )
        return RetrievalResult(chunks=chunks, backend="moss")

    async def index(self, chunks: list[RetrievedChunk]) -> None:
        """Upsert *chunks* into the Moss index (idempotent by chunk_id).

        Note: the generic ``RetrievedChunk`` carries no ``organization_id``; the
        ingestion/seed paths use :meth:`index_with_org` to stamp tenant metadata. This
        method indexes without an org filter and is kept for interface completeness.
        """
        await self.index_with_org(chunks, org_id=settings.default_org_id)

    async def index_with_org(self, chunks: list[RetrievedChunk], *, org_id: str) -> None:
        """Upsert *chunks* tagged with ``organization_id=org_id`` for tenant-scoped query."""
        if not chunks:
            return
        from moss import DocumentInfo, MutationOptions

        docs = [
            DocumentInfo(
                id=c.chunk_id,
                text=c.text,
                metadata={
                    "document_id": c.document_id,
                    "title": c.title,
                    "organization_id": org_id,
                },
            )
            for c in chunks
        ]
        # Create the index on first write; thereafter upsert.
        try:
            await self._client.get_index(self._index)
            exists = True
        except Exception:
            exists = False

        if exists:
            await self._client.add_docs(self._index, docs, MutationOptions(upsert=True))
        else:
            await self._client.create_index(self._index, docs, model_id=self._model_id)
        self._loaded = False  # force a reload so new docs are queryable
        logger.info("moss_index_ok", extra={"n": len(docs), "org_id": org_id})

    async def delete(self, document_id: str) -> None:
        """Best-effort delete by document.

        Moss ``delete_docs`` takes chunk ids; mapping a ``document_id`` to its chunk ids
        requires the system-of-record (Postgres ``chunks``). The ingestion pipeline deletes
        the Postgres rows and re-``index`` upserts by chunk_id, so re-ingest stays correct
        for unchanged chunk sets. # TODO: track chunk ids per document to hard-delete from Moss.
        """
        logger.info("moss_delete_noop", extra={"document_id": document_id})

    async def aclose(self) -> None:
        """No persistent connection to close (the SDK manages its own transport)."""
        return None

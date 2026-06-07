"""Retrieval service interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel


class RetrievedChunk(BaseModel):
    """A single chunk returned by a retrieval backend."""
    chunk_id: str       # chk_…
    document_id: str    # doc_…
    title: str          # document title
    text: str           # full chunk text
    snippet: str        # short excerpt for display (<=200 chars)
    score: float        # cosine similarity / BM25 score, 0..1
    moss_ref: str | None = None   # handle in the Moss index (None for pgvector-only results)


class RetrievalResult(BaseModel):
    """Result returned by RetrievalService.query()."""
    chunks: list[RetrievedChunk]
    backend: Literal["moss", "pgvector"]


class RetrievalService(ABC):
    """Abstract retrieval service — Moss primary, pgvector fallback."""

    @abstractmethod
    async def query(
        self,
        org_id: str,
        text: str,
        k: int = 5,
    ) -> RetrievalResult:
        """Retrieve the top-k chunks most relevant to *text* for *org_id*.

        Returns a RetrievalResult indicating which backend served the request.
        Must complete within the live-path latency budget (~200 ms).
        """
        ...

    @abstractmethod
    async def index(self, chunks: list[RetrievedChunk]) -> None:
        """Write *chunks* into the retrieval index.

        Called by the ingestion pipeline after embedding. Must be idempotent
        (re-indexing the same chunk_id replaces the previous entry).
        """
        ...

    @abstractmethod
    async def delete(self, document_id: str) -> None:
        """Remove all chunks for *document_id* from the index.

        Called on document deletion. Must be idempotent.
        """
        ...

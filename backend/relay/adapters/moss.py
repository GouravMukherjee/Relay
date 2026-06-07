"""Moss retrieval adapter.

Implements ``RetrievalService`` using the Moss semantic-search API.

Required creds: ``moss_api_key``, ``moss_base_url``.

# TODO: confirm <Moss> API — endpoints, request/response shapes, and auth
# scheme below are best-guess from Moss public docs; verify against the actual
# API before shipping.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from relay.config import settings
from relay.interfaces.retrieval import RetrievalResult, RetrievedChunk, RetrievalService

logger = logging.getLogger(__name__)


class MossRetrieval(RetrievalService):
    """Retrieval service backed by the Moss semantic search API.

    Moss is the primary (fast, <10 ms) retrieval path. On error the
    ``CompositeRetrievalService`` automatically falls back to pgvector.

    Required settings
    -----------------
    moss_api_key    : str  — API key passed as ``Authorization: Bearer …``
    moss_base_url   : str  — Base URL, e.g. ``https://api.moss.ai``
    """

    def __init__(self) -> None:
        if not settings.moss_api_key:
            raise RuntimeError(
                "MossRetrieval requires MOSS_API_KEY to be set in the environment."
            )
        self._base_url = settings.moss_base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {settings.moss_api_key}",
            "Content-Type": "application/json",
        }
        # Shared async client — callers must be inside an async context.
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=httpx.Timeout(5.0),  # keep well within live-path budget
        )

    # ------------------------------------------------------------------
    # RetrievalService interface
    # ------------------------------------------------------------------

    async def query(
        self,
        org_id: str,
        text: str,
        k: int = 5,
    ) -> RetrievalResult:
        """Query Moss for the top-*k* chunks most relevant to *text*.

        # TODO: confirm <Moss> API — verify /search path, payload keys, and
        # response shape (``results[].id``, ``results[].score``, etc.).
        """
        payload: dict[str, Any] = {
            "query": text,
            "namespace": org_id,  # tenant isolation via Moss namespace
            "top_k": k,
        }
        response = await self._client.post("/v1/search", json=payload)
        response.raise_for_status()
        data = response.json()

        chunks: list[RetrievedChunk] = []
        # TODO: confirm <Moss> API — exact field names in each result object.
        for item in data.get("results", []):
            text_content: str = item.get("text", "")
            chunks.append(
                RetrievedChunk(
                    chunk_id=item.get("id", ""),
                    document_id=item.get("document_id", ""),
                    title=item.get("title", ""),
                    text=text_content,
                    snippet=text_content[:200],
                    score=float(item.get("score", 0.0)),
                    moss_ref=item.get("ref") or item.get("id"),
                )
            )
        return RetrievalResult(chunks=chunks, backend="moss")

    async def index(self, chunks: list[RetrievedChunk]) -> None:
        """Upsert *chunks* into the Moss index.

        # TODO: confirm <Moss> API — verify /index (or /upsert) path and
        # payload shape. The ``moss_ref`` field is populated from the API
        # response if the server assigns a stable handle.
        """
        if not chunks:
            return

        # Derive namespace from the first chunk's document_id prefix;
        # callers ensure all chunks in a batch belong to the same org.
        docs: list[dict[str, Any]] = [
            {
                "id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "text": chunk.text,
                "title": chunk.title,
                "moss_ref": chunk.moss_ref,
                "metadata": {},
            }
            for chunk in chunks
        ]
        # TODO: confirm <Moss> API — namespace/org isolation mechanism for index.
        payload: dict[str, Any] = {"documents": docs}
        response = await self._client.post("/v1/index", json=payload)
        response.raise_for_status()
        logger.info(
            "moss_index_ok",
            extra={"chunk_count": len(chunks)},
        )

    async def delete(self, document_id: str) -> None:
        """Remove all chunks for *document_id* from Moss.

        # TODO: confirm <Moss> API — verify /delete path and payload.
        """
        # TODO: confirm <Moss> API — body shape for bulk delete by document_id.
        payload: dict[str, Any] = {"document_id": document_id}
        response = await self._client.post("/v1/delete", json=payload)
        response.raise_for_status()
        logger.info("moss_delete_ok", extra={"document_id": document_id})

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Call on application shutdown."""
        await self._client.aclose()

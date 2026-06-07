"""Composite retrieval service: Moss primary, pgvector fallback.

Architecture invariant (CLAUDE.md): the live path queries this pre-built index only —
it never touches raw files. Moss is the primary backend because cold pgvector lookups
(97-307 ms) blow the ~200 ms voice budget; pgvector is the resilience fallback when Moss
errors or returns nothing usable.

``RetrievalResult.backend`` always reflects which backend actually served the request, so
callers (the WS hub's ``session.status`` event) can surface it.

``index`` / ``delete`` fan out to BOTH backends: Moss gets the index write, and the
pgvector path persists ``chunks`` rows (with embeddings) so the fallback can serve.
"""
from __future__ import annotations

from relay.interfaces.retrieval import (
    RetrievalResult,
    RetrievalService,
    RetrievedChunk,
)
from relay.logging import get_logger, log_latency

logger = get_logger(__name__)


class CompositeRetrievalService(RetrievalService):
    """Wraps a primary (Moss) and a fallback (pgvector) :class:`RetrievalService`.

    Both backends are injected so the sponsor adapters stay behind the interface
    (CLAUDE.md invariant #4 — never hardcode a vendor call in business logic). Use
    :meth:`from_settings` to build the production composition from the env-configured
    adapters.
    """

    def __init__(
        self,
        primary: RetrievalService,
        fallback: RetrievalService,
    ) -> None:
        """Args:
        primary:  The fast primary backend (Moss in production).
        fallback: The resilience backend (pgvector in production).
        """
        self._primary = primary
        self._fallback = fallback

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def from_settings(cls) -> "CompositeRetrievalService":
        """Build the production composition (Moss primary + pgvector fallback).

        Adapters are imported lazily so importing this module never requires the
        adapter package (which validates creds at construction) to be present.
        """
        from relay.adapters.pgvector_retrieval import PgVectorRetrieval

        fallback = PgVectorRetrieval()
        # Moss is primary when its key is configured; if construction fails (missing/bad
        # key), degrade to pgvector-only instead of 500ing the whole query path.
        try:
            from relay.adapters.moss import MossRetrieval

            primary: RetrievalService = MossRetrieval()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Moss unavailable; using pgvector-only retrieval",
                extra={"error": str(exc)},
            )
            primary = fallback
        return cls(primary=primary, fallback=fallback)

    # ------------------------------------------------------------------
    # RetrievalService
    # ------------------------------------------------------------------
    async def query(
        self,
        org_id: str,
        text: str,
        k: int = 5,
    ) -> RetrievalResult:
        """Retrieve top-k chunks for *org_id*. Moss first; on error or empty, pgvector.

        ``RetrievalResult.backend`` reflects whichever backend produced the returned
        chunks. A Moss failure is logged and swallowed so the live path degrades to
        pgvector rather than dropping the card entirely.
        """
        import time

        start = time.perf_counter()
        try:
            result = await self._primary.query(org_id, text, k=k)
            if result.chunks:
                log_latency(
                    logger,
                    "retrieval",
                    latency_ms=(time.perf_counter() - start) * 1000.0,
                    backend="moss",
                    org_id=org_id,
                    k=k,
                    n=len(result.chunks),
                )
                # Normalise the reported backend regardless of what the adapter set.
                return RetrievalResult(chunks=result.chunks, backend="moss")
            # Empty from Moss -> try the fallback (the index may not be warm yet).
            logger.info(
                "moss returned no chunks; falling back to pgvector",
                extra={"org_id": org_id},
            )
        except Exception as exc:  # noqa: BLE001 — degrade gracefully on any Moss error.
            logger.warning(
                "moss query failed; falling back to pgvector",
                extra={"org_id": org_id, "error": str(exc)},
            )

        if self._fallback is self._primary:
            # Moss-only composition (no distinct fallback) — nothing more to try.
            return RetrievalResult(chunks=[], backend="moss")
        try:
            fallback_result = await self._fallback.query(org_id, text, k=k)
        except Exception as exc:  # noqa: BLE001 — e.g. embeddings unavailable
            logger.warning(
                "pgvector fallback failed; returning empty result",
                extra={"org_id": org_id, "error": str(exc)},
            )
            return RetrievalResult(chunks=[], backend="pgvector")
        log_latency(
            logger,
            "retrieval",
            latency_ms=(time.perf_counter() - start) * 1000.0,
            backend="pgvector",
            org_id=org_id,
            k=k,
            n=len(fallback_result.chunks),
        )
        return RetrievalResult(chunks=fallback_result.chunks, backend="pgvector")

    async def index(self, chunks: list[RetrievedChunk]) -> None:
        """Fan out indexing to both backends (idempotent per chunk_id).

        pgvector indexing persists ``chunks`` rows with embeddings; Moss indexing
        writes its own index. Both are required so the fallback can serve. The
        fallback write is attempted even if the Moss write fails, so the pgvector
        path is never silently empty.
        """
        if not chunks:
            return
        primary_error: Exception | None = None
        try:
            await self._primary.index(chunks)
        except Exception as exc:  # noqa: BLE001
            primary_error = exc
            logger.warning(
                "moss index failed; pgvector rows still written",
                extra={"n": len(chunks), "error": str(exc)},
            )
        await self._fallback.index(chunks)
        if primary_error is not None:
            # Surface the Moss failure once pgvector durability is guaranteed.
            raise primary_error

    async def delete(self, document_id: str) -> None:
        """Remove all chunks for *document_id* from both backends (idempotent)."""
        primary_error: Exception | None = None
        try:
            await self._primary.delete(document_id)
        except Exception as exc:  # noqa: BLE001
            primary_error = exc
            logger.warning(
                "moss delete failed; pgvector delete still attempted",
                extra={"document_id": document_id, "error": str(exc)},
            )
        await self._fallback.delete(document_id)
        if primary_error is not None:
            raise primary_error


__all__ = ["CompositeRetrievalService"]

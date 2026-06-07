"""The grounded synthesis orchestrator.

Turns a query (a fired trigger or a manual query) into a grounded, cited :class:`Card`,
or returns ``None`` ("no card"). This is where the **grounding guard** lives — the most
important invariant in the system (CLAUDE.md: *grounded or silent, never hallucinate*).

Flow (``synthesize``):
  1. ``retrieval.query`` -> chunks. If empty -> return ``None`` (no grounding material).
  2. (Desk only) augment context with ``memory.recall(customer_id)`` — NOT a grounding
     source, only conversational context passed to the LLM as ``window`` tone hints.
  3. ``llm.synthesize_card`` -> CardDraft, or ``None`` if no chunk is relevant -> return
     ``None``.
  4. Build a Card that cites ONLY retrieved chunks (the LLM's ``used_chunk_ids`` filtered
     against the actually-retrieved set — a citation can never reference a chunk that was
     not retrieved). Persist the Card + CardSource rows. ``latency_ms`` = elapsed wall time.

Latency uses an injected clock (``time.perf_counter`` by default) so it is testable and
never calls ``datetime.now`` at import time.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from relay.db.models import Card as CardModel
from relay.db.models import CardSource as CardSourceModel
from relay.ids import new_id
from relay.interfaces.llm import LLMClient
from relay.interfaces.retrieval import RetrievalService, RetrievedChunk
from relay.logging import get_logger, log_latency
from relay.memory.service import MemoryService
from relay.schemas.cards import Card, Source

logger = get_logger(__name__)

# Callback the streaming path uses to push card.new / card.update envelopes out
# through whatever transport the caller owns (the WsHub, in production).
EmitFn = Callable[[str, dict[str, Any]], Awaitable[None]]


class Orchestrator:
    """Grounded synthesis: retrieve -> (recall) -> synthesize -> cite -> persist.

    Args:
        retrieval: The composite retrieval service (Moss + pgvector fallback).
        llm:       The LLM client (TFY gateway / Claude). MUST honour the grounding
                   contract — returns ``None`` when no chunk is relevant.
        session:   The :class:`AsyncSession` used to persist the Card + CardSource rows.
                   Request path = RLS-scoped session; worker path = privileged session.
        memory:    Optional per-customer memory service (Desk mode context augmentation).
        clock:     Monotonic clock returning seconds; injected for testability. Defaults
                   to :func:`time.perf_counter`.
    """

    def __init__(
        self,
        retrieval: RetrievalService,
        llm: LLMClient,
        session: AsyncSession,
        memory: MemoryService | None = None,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self._retrieval = retrieval
        self._llm = llm
        self._session = session
        self._memory = memory
        self._clock = clock

    async def synthesize(
        self,
        *,
        session_id: str,
        org_id: str,
        mode: str,
        query_text: str,
        customer_id: str | None = None,
        emit: EmitFn | None = None,
    ) -> Card | None:
        """Synthesize a grounded, cited Card for *query_text*, or ``None`` ("no card").

        Returns ``None`` (without persisting anything) when retrieval is empty or the
        LLM declines — never a fabricated answer.

        When *emit* is provided, the answer is streamed token-by-token: an initial
        ``card.new`` envelope is pushed as soon as the first token arrives, followed by
        ``card.update`` envelopes as the answer grows, then a final ``card.update`` with
        the cited sources + measured latency. The same persisted :class:`Card` is also
        returned. When *emit* is ``None`` the call is fully synchronous (REST path).
        """
        start = self._clock()

        # 1) Retrieve grounding material. No chunks -> no card.
        result = await self._retrieval.query(org_id, query_text, k=5)
        chunks = result.chunks
        if not chunks:
            logger.info(
                "no grounding chunks; returning no card",
                extra={"session_id": session_id, "mode": mode, "backend": result.backend},
            )
            return None

        # 2) Desk mode: pull customer memory for conversational context only.
        #    Memory is NEVER grounding — it is passed to the LLM as `window` tone hints.
        window: list[str] | None = None
        if mode == "desk" and self._memory is not None and customer_id:
            try:
                memories = await self._memory.recall(customer_id, query_text, k=5)
                if memories:
                    window = [m.text for m in memories]
            except Exception as exc:  # noqa: BLE001 — memory is best-effort, never fatal.
                logger.warning(
                    "memory recall failed; proceeding without it",
                    extra={"session_id": session_id, "error": str(exc)},
                )

        if emit is not None:
            return await self._synthesize_streaming(
                session_id=session_id,
                org_id=org_id,
                mode=mode,
                query_text=query_text,
                window=window,
                chunks=chunks,
                backend=result.backend,
                start=start,
                emit=emit,
            )

        # 3) Synthesize. None == the LLM found nothing relevant in the chunks -> no card.
        draft = await self._llm.synthesize_card(
            query=query_text,
            chunks=chunks,
            mode=mode,
            window=window,
        )
        if draft is None:
            logger.info(
                "llm declined (no relevant chunk); returning no card",
                extra={"session_id": session_id, "mode": mode},
            )
            return None

        cited_ids = self._cited_ids(draft.used_chunk_ids, chunks)
        elapsed_ms = int((self._clock() - start) * 1000.0)
        created_at = datetime.now(timezone.utc)

        card_model, sources = await self._persist_card(
            session_id=session_id,
            org_id=org_id,
            mode=mode,
            answer=draft.answer,
            title=draft.title,
            query_text=query_text,
            elapsed_ms=elapsed_ms,
            created_at=created_at,
            cited_ids=cited_ids,
            chunks=chunks,
        )

        log_latency(
            logger,
            "synthesize",
            latency_ms=float(elapsed_ms),
            session_id=session_id,
            mode=mode,
            backend=result.backend,
            n_sources=len(sources),
        )

        return Card(
            card_id=card_model.id,
            session_id=session_id,
            mode=mode,
            title=draft.title,
            answer=draft.answer,
            sources=sources,
            trigger_text=query_text,
            latency_ms=elapsed_ms,
            created_at=created_at.isoformat(),
        )

    # ------------------------------------------------------------------
    # Streaming path
    # ------------------------------------------------------------------

    async def _synthesize_streaming(
        self,
        *,
        session_id: str,
        org_id: str,
        mode: str,
        query_text: str,
        window: list[str] | None,
        chunks: list[RetrievedChunk],
        backend: str,
        start: float,
        emit: EmitFn,
    ) -> Card | None:
        """Stream the answer out via *emit*, persist the final Card, and return it."""
        card_id = new_id("card")
        created_at = datetime.now(timezone.utc)
        # Preliminary sources (the retrieved chunks) so the card paints as grounded
        # immediately; the final update narrows them to the actually-cited set.
        prelim_sources = [
            Source(
                document_id=c.document_id,
                title=c.title,
                snippet=c.snippet,
                score=float(c.score),
            )
            for c in chunks[:5]
        ]

        answer = ""
        draft = None
        emitted_new = False

        async for ev in self._llm.synthesize_card_stream(
            query=query_text, chunks=chunks, mode=mode, window=window
        ):
            if ev.delta:
                answer += ev.delta
                if not emitted_new:
                    emitted_new = True
                    await emit(
                        "card.new",
                        {
                            "card_id": card_id,
                            "session_id": session_id,
                            "mode": mode,
                            "title": None,
                            "answer": answer,
                            "sources": [s.model_dump() for s in prelim_sources],
                            "trigger_text": query_text,
                            "latency_ms": 0,
                            "created_at": created_at.isoformat(),
                        },
                    )
                else:
                    await emit("card.update", {"card_id": card_id, "answer": answer})
            if ev.done:
                draft = ev.draft

        final_answer = (draft.answer if (draft and draft.answer) else answer).strip()
        if not final_answer:
            # Grounding guard: nothing groundable was produced -> no card.
            logger.info(
                "stream produced no groundable answer; no card",
                extra={"session_id": session_id, "mode": mode},
            )
            return None

        cited_ids = self._cited_ids(draft.used_chunk_ids if draft else [], chunks)
        title = draft.title if draft else None
        elapsed_ms = int((self._clock() - start) * 1000.0)

        card_model, sources = await self._persist_card(
            session_id=session_id,
            org_id=org_id,
            mode=mode,
            answer=final_answer,
            title=title,
            query_text=query_text,
            elapsed_ms=elapsed_ms,
            created_at=created_at,
            cited_ids=cited_ids,
            chunks=chunks,
            card_id=card_id,
        )

        # Final update: settle answer, narrow to cited sources, stamp real latency.
        await emit(
            "card.update",
            {
                "card_id": card_id,
                "title": title,
                "answer": final_answer,
                "sources": [s.model_dump() for s in sources],
                "latency_ms": elapsed_ms,
            },
        )

        log_latency(
            logger,
            "synthesize_stream",
            latency_ms=float(elapsed_ms),
            session_id=session_id,
            mode=mode,
            backend=backend,
            n_sources=len(sources),
        )

        return Card(
            card_id=card_model.id,
            session_id=session_id,
            mode=mode,
            title=title,
            answer=final_answer,
            sources=sources,
            trigger_text=query_text,
            latency_ms=elapsed_ms,
            created_at=created_at.isoformat(),
        )

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cited_ids(
        used_chunk_ids: list[str], chunks: list[RetrievedChunk]
    ) -> list[str]:
        """Filter the model's cited ids to the retrieved set (drop hallucinated cites).

        If nothing valid remains, fall back to citing every retrieved chunk so a card
        is never uncited.
        """
        by_id = {c.chunk_id for c in chunks}
        cited = [cid for cid in used_chunk_ids if cid in by_id]
        return cited or [c.chunk_id for c in chunks]

    async def _persist_card(
        self,
        *,
        session_id: str,
        org_id: str,
        mode: str,
        answer: str,
        title: str | None,
        query_text: str,
        elapsed_ms: int,
        created_at: datetime,
        cited_ids: list[str],
        chunks: list[RetrievedChunk],
        card_id: str | None = None,
    ) -> tuple[CardModel, list[Source]]:
        """Persist the Card + CardSource rows and build the external Source list."""
        by_id: dict[str, RetrievedChunk] = {c.chunk_id: c for c in chunks}

        card_kwargs: dict[str, Any] = dict(
            session_id=session_id,
            organization_id=org_id,
            mode=mode,
            answer=answer,
            title=title,
            trigger_text=query_text,
            latency_ms=elapsed_ms,
            created_at=created_at,
        )
        if card_id is not None:
            card_kwargs["id"] = card_id
        card_model = CardModel(**card_kwargs)
        self._session.add(card_model)
        await self._session.flush()  # assign card_model.id before CardSource rows

        sources: list[Source] = []
        for cid in cited_ids:
            chunk = by_id[cid]
            self._session.add(
                CardSourceModel(
                    card_id=card_model.id,
                    chunk_id=cid,
                    organization_id=org_id,
                    score=float(chunk.score),
                )
            )
            sources.append(
                Source(
                    document_id=chunk.document_id,
                    title=chunk.title,
                    snippet=chunk.snippet,
                    score=float(chunk.score),
                )
            )
        await self._session.flush()
        return card_model, sources


__all__ = ["Orchestrator"]

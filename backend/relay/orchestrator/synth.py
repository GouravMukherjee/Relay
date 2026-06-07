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

from collections.abc import Callable
from datetime import datetime, timezone
from time import perf_counter

from sqlalchemy.ext.asyncio import AsyncSession

from relay.db.models import Card as CardModel
from relay.db.models import CardSource as CardSourceModel
from relay.interfaces.llm import LLMClient
from relay.interfaces.retrieval import RetrievalService, RetrievedChunk
from relay.logging import get_logger, log_latency
from relay.memory.service import MemoryService
from relay.schemas.cards import Card, Source

logger = get_logger(__name__)


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
    ) -> Card | None:
        """Synthesize a grounded, cited Card for *query_text*, or ``None`` ("no card").

        Returns ``None`` (without persisting anything) when retrieval is empty or the
        LLM declines — never a fabricated answer.
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

        # 4) Build sources citing ONLY retrieved chunks. A cited id that was not in the
        #    retrieved set is dropped (the guard against hallucinated citations). If the
        #    model cited nothing valid, fall back to citing all retrieved chunks so the
        #    answer is never uncited.
        by_id: dict[str, RetrievedChunk] = {c.chunk_id: c for c in chunks}
        cited_ids = [cid for cid in draft.used_chunk_ids if cid in by_id]
        if not cited_ids:
            cited_ids = list(by_id.keys())

        elapsed_ms = int((self._clock() - start) * 1000.0)
        created_at = datetime.now(timezone.utc)

        # Persist the Card.
        card_model = CardModel(
            session_id=session_id,
            organization_id=org_id,
            mode=mode,
            answer=draft.answer,
            title=draft.title,
            trigger_text=query_text,
            latency_ms=elapsed_ms,
            created_at=created_at,
        )
        self._session.add(card_model)
        await self._session.flush()  # assign card_model.id before writing CardSource rows

        # Persist CardSource rows + build the external Source list (cited order preserved).
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


__all__ = ["Orchestrator"]

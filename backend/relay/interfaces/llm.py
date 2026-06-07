"""LLM client interface."""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from relay.interfaces.retrieval import RetrievedChunk


class CardDraft(BaseModel):
    """Raw output from the LLM before it is persisted as a Card.

    ``used_chunk_ids`` is the subset of provided chunk IDs that the model
    actually cited in its answer — used to build CardSource rows.
    """
    answer: str
    title: str | None = None
    used_chunk_ids: list[str]


class LLMClient(ABC):
    """Abstract LLM client — TrueFoundry gateway (Claude primary) in production.

    The grounding contract is absolute: the implementation MUST answer only
    from provided chunks and cite them. If the provided chunks contain no
    relevant information, the implementation MUST return None ("no card").
    """

    @abstractmethod
    async def synthesize_card(
        self,
        *,
        query: str,
        chunks: list[RetrievedChunk],
        mode: str,
        window: list[str] | None = None,
    ) -> CardDraft | None:
        """Synthesise a grounded answer from the retrieved chunks.

        Args:
            query:   The question or trigger text that prompted retrieval.
            chunks:  Retrieved chunks — the ONLY allowed source material.
            mode:    Session mode ("live" | "desk" | "intake") — may affect
                     response style/length.
            window:  Optional recent transcript lines for conversation context
                     (Desk mode). Must NOT be used as a grounding source —
                     only for tone/context.

        Returns:
            CardDraft if the chunks contain a relevant answer, or None if
            no chunk is sufficiently relevant (the orchestrator will return
            "no card" without persisting anything).
        """
        ...

"""Per-customer memory service (Desk mode).

Stores and recalls short ``Memory`` rows (facts / summaries / preferences) attached to a
``Customer``. Recall is a pgvector cosine search over ``memories.embedding`` (1024-d),
scoped to the customer. The orchestrator may use recalled memories to augment context in
Desk mode — but memories are NEVER a grounding source. Grounding always comes from the
retrieved document chunks; memory only shapes which question gets asked / tone.

The service is constructed with an :class:`Embeddings` adapter and operates against an
injected :class:`AsyncSession`. Org scoping: ``Memory.organization_id`` is denormalised, so
the request-scoped RLS session already constrains rows to the caller's org. Callers using
the privileged (RLS-bypassing) session MUST still pass / filter by org explicitly — the
``store`` API takes ``org_id`` for exactly that reason.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from relay.db.models import Memory
from relay.interfaces.embeddings import Embeddings
from relay.logging import get_logger

logger = get_logger(__name__)


class MemoryService:
    """Semantic recall + store over the ``memories`` table.

    Args:
        embeddings: Embeddings adapter (1024-d) used to embed both stored memories
            and recall queries.
        session: The :class:`AsyncSession` to operate on. In the request path this is
            the RLS-scoped session; in workers it is the privileged session (caller
            scopes by org).
    """

    def __init__(self, embeddings: Embeddings, session: AsyncSession) -> None:
        self._embeddings = embeddings
        self._session = session

    async def recall(
        self,
        customer_id: str,
        text: str,
        k: int = 5,
    ) -> list[Memory]:
        """Return the *k* memories for *customer_id* most similar to *text*.

        Embeds *text*, then runs a pgvector cosine-distance ordering over the
        customer's memory rows. Rows without an embedding are skipped. Returns an
        empty list if the customer has no embedded memories.
        """
        if not text:
            return []
        [query_vec] = await self._embeddings.embed([text])

        stmt = (
            select(Memory)
            .where(
                Memory.customer_id == customer_id,
                Memory.embedding.is_not(None),
            )
            # pgvector cosine distance; nearest first.
            .order_by(Memory.embedding.cosine_distance(query_vec))
            .limit(k)
        )
        result = await self._session.execute(stmt)
        memories = list(result.scalars().all())
        logger.info(
            "memory recall",
            extra={"customer_id": customer_id, "k": k, "n": len(memories)},
        )
        return memories

    async def store(
        self,
        customer_id: str,
        kind: str,
        text: str,
        org_id: str,
        source_session_id: str | None = None,
    ) -> Memory:
        """Persist a new memory for *customer_id* and return it.

        Embeds *text* up front so recall works immediately. ``kind`` is one of
        ``fact`` | ``summary`` | ``preference``. ``org_id`` denormalises onto the row
        (required for RLS + privileged scoping). Idempotency is not implied — callers
        that re-derive the same summary should dedupe upstream.
        """
        [vec] = await self._embeddings.embed([text])
        memory = Memory(
            customer_id=customer_id,
            organization_id=org_id,
            kind=kind,
            text=text,
            embedding=vec,
            source_session_id=source_session_id,
        )
        self._session.add(memory)
        await self._session.flush()  # populate PK / defaults without ending the txn
        logger.info(
            "memory stored",
            extra={"customer_id": customer_id, "kind": kind, "memory_id": memory.id},
        )
        return memory


__all__ = ["MemoryService"]

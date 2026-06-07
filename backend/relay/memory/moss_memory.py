"""Moss-backed per-customer memory (Desk mode).

A drop-in alternative to :class:`relay.memory.service.MemoryService` that uses a
dedicated Moss index (``settings.moss_memory_index_name``) instead of pgvector. Moss has
built-in embeddings, so this works without the TFY embeddings service. Memories are scoped
per customer via a metadata filter (``customer_id``), mirroring the ``remember_fact`` /
``recall_facts`` pattern from the LiveKit + Moss starter.

Contract match: ``recall`` returns objects exposing ``.text`` (what the Orchestrator reads
for Desk ``window`` context); memory is NEVER a grounding source — only the retrieved
document chunks ground a card.
"""
from __future__ import annotations

from dataclasses import dataclass

from relay.config import settings
from relay.ids import new_id
from relay.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RecalledMemory:
    """Lightweight recalled memory (only ``.text`` is consumed by the orchestrator)."""

    text: str
    kind: str
    score: float


class MossMemoryService:
    """Per-customer memory recall/store over a Moss index (built-in embeddings).

    Constructed from settings (no DB session needed). Tenant + customer scoping is via
    Moss metadata filters, so cross-customer leakage is prevented at the query.
    """

    def __init__(self) -> None:
        if not settings.moss_project_id or not settings.moss_project_key:
            raise RuntimeError(
                "MossMemoryService requires MOSS_PROJECT_ID and MOSS_PROJECT_KEY."
            )
        from moss import MossClient

        self._client = MossClient(settings.moss_project_id, settings.moss_project_key)
        self._index = settings.moss_memory_index_name
        self._loaded = False

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            await self._client.load_index(self._index, auto_refresh=True)
            self._loaded = True
        except Exception as exc:  # index may not exist yet (no memories stored)
            logger.info("memory index not loadable yet", extra={"error": str(exc)})

    async def recall(self, customer_id: str, text: str, k: int = 5) -> list[RecalledMemory]:
        """Return up to *k* memories for *customer_id* most relevant to *text*.

        Returns an empty list when the index/customer has no memories (best-effort;
        the orchestrator treats memory as optional context).
        """
        if not text or not customer_id:
            return []
        from moss import QueryOptions

        await self._ensure_loaded()
        if not self._loaded:
            return []
        try:
            res = await self._client.query(
                self._index,
                text,
                QueryOptions(
                    top_k=k,
                    filter={"field": "customer_id", "condition": {"$eq": customer_id}},
                ),
            )
        except Exception as exc:  # noqa: BLE001 — memory is best-effort
            logger.warning("memory recall failed", extra={"error": str(exc)})
            return []
        out = [
            RecalledMemory(
                text=d.text,
                kind=str((d.metadata or {}).get("kind", "fact")),
                score=float(d.score),
            )
            for d in res.docs
        ]
        logger.info("memory recall", extra={"customer_id": customer_id, "n": len(out)})
        return out

    async def store(
        self,
        customer_id: str,
        kind: str,
        text: str,
        org_id: str,
        source_session_id: str | None = None,
    ) -> str:
        """Store a memory for *customer_id* in the Moss memory index. Returns its id."""
        from moss import DocumentInfo, MutationOptions

        mem_id = new_id("mem")
        doc = DocumentInfo(
            id=mem_id,
            text=text,
            metadata={
                "customer_id": customer_id,
                "organization_id": org_id,
                "kind": kind,
                "source_session_id": source_session_id or "",
            },
        )
        try:
            await self._client.get_index(self._index)
            exists = True
        except Exception:
            exists = False
        if exists:
            await self._client.add_docs(self._index, [doc], MutationOptions(upsert=True))
        else:
            await self._client.create_index(self._index, [doc])
        self._loaded = False
        logger.info("memory stored", extra={"customer_id": customer_id, "kind": kind, "memory_id": mem_id})
        return mem_id


__all__ = ["MossMemoryService", "RecalledMemory"]

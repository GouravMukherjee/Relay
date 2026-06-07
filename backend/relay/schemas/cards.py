"""Card and Source schemas — mirror types.ts Card / Source exactly."""
from __future__ import annotations

from pydantic import BaseModel


# ── Shared object shapes ──────────────────────────────────────────────────────

class Source(BaseModel):
    document_id: str   # doc_…
    title: str
    snippet: str
    score: float       # retrieval score, 0..1


class Card(BaseModel):
    card_id: str       # card_…
    session_id: str | None = None    # ses_… (None for session-less manual queries)
    mode: str          # live | desk | intake
    title: str | None = None   # short headline, e.g. "99.9% uptime SLA"
    answer: str
    sources: list[Source]
    trigger_text: str
    latency_ms: int
    created_at: str    # ISO-8601 UTC


# ── Response models ───────────────────────────────────────────────────────────

class SessionCardsResponse(BaseModel):
    """GET /sessions/{session_id}/cards response."""
    cards: list[Card]


# ── Mapper from DB models ─────────────────────────────────────────────────────

def source_from_card_source(cs: object, chunk: object, doc_title: str = "") -> Source:
    """Map a CardSource + Chunk ORM pair to a Source schema.

    Args:
        cs:        relay.db.models.CardSource ORM instance.
        chunk:     relay.db.models.Chunk ORM instance (joined).
        doc_title: Title of the parent Document; callers should resolve this from
                   ``chunk.document.title`` (if the relationship is loaded) or pass
                   it from a separately-fetched Document row.  Falls back to the
                   empty string if omitted so the mapper is always safe to call.
    """
    # Prefer a pre-loaded relationship, then the caller-supplied fallback.
    doc = getattr(chunk, "document", None)  # type: ignore[attr-defined]
    title = (doc.title if doc is not None else None) or doc_title
    return Source(
        document_id=chunk.document_id,  # type: ignore[attr-defined]
        title=title,
        snippet=chunk.text[:200] if chunk.text else "",  # type: ignore[attr-defined]
        score=float(cs.score or 0.0),  # type: ignore[attr-defined]
    )


def card_to_schema(card: object, sources: list[Source]) -> Card:
    """Map a relay.db.models.Card ORM instance + resolved sources to Card schema.

    The DB model uses ``id`` as its PK; the external schema exposes it
    as ``card_id``.
    """
    return Card(
        card_id=card.id,  # type: ignore[attr-defined]
        session_id=card.session_id,  # type: ignore[attr-defined]
        mode=card.mode,  # type: ignore[attr-defined]
        title=getattr(card, "title", None),  # type: ignore[attr-defined]
        answer=card.answer,  # type: ignore[attr-defined]
        sources=sources,
        trigger_text=card.trigger_text,  # type: ignore[attr-defined]
        latency_ms=card.latency_ms or 0,  # type: ignore[attr-defined]
        created_at=card.created_at.isoformat() if card.created_at else "",  # type: ignore[attr-defined]
    )

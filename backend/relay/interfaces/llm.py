"""LLM client interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Literal

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


class LeadExtraction(BaseModel):
    """Structured lead fields extracted from an Intake-call transcript.

    All fields optional — the model fills what the conversation has surfaced so far.
    Budget/Authority/Need/Timeline are the BANT qualifiers; name/company/email are
    the lead's identity.
    """

    name: str | None = None
    company: str | None = None
    email: str | None = None
    budget: str | None = None
    authority: str | None = None
    need: str | None = None
    timeline: str | None = None


class CardStreamEvent(BaseModel):
    """One step of a streaming card synthesis.

    The stream yields zero or more ``delta`` events (incremental *display* text —
    the model's running answer with any trailing citation block stripped), then
    exactly one terminal event with ``done=True``. The terminal event carries the
    fully-parsed :class:`CardDraft` (or ``None`` when the model declined to ground
    an answer — the grounding guard / "no card").
    """

    delta: str = ""
    done: bool = False
    draft: CardDraft | None = None


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

    async def synthesize_card_stream(
        self,
        *,
        query: str,
        chunks: list[RetrievedChunk],
        mode: str,
        window: list[str] | None = None,
    ) -> AsyncIterator[CardStreamEvent]:
        """Stream a grounded answer token-by-token.

        Default implementation: call :meth:`synthesize_card` and emit the whole
        answer as a single delta. Adapters that support server-side streaming
        (e.g. the TFY gateway) override this to yield incremental deltas so the
        UI can paint the first token immediately. The contract is identical:
        the terminal event's ``draft`` is ``None`` when no chunk is relevant.
        """
        draft = await self.synthesize_card(
            query=query, chunks=chunks, mode=mode, window=window
        )
        if draft is not None and draft.answer:
            yield CardStreamEvent(delta=draft.answer)
        yield CardStreamEvent(done=True, draft=draft)

    async def extract_lead(self, *, transcript: str) -> "LeadExtraction":
        """Extract lead identity + BANT qualifiers from an Intake transcript.

        Default implementation: a lightweight regex/keyword heuristic so the feature
        degrades gracefully without an LLM. Real adapters (TFY gateway) override this
        with an LLM extraction for far better recall.
        """
        return _heuristic_extract_lead(transcript)

    async def classify_intent(self, *, text: str) -> Literal["support", "sales", "it"]:
        """Classify a customer message as ``"support"``, ``"sales"``, or ``"it"`` intent.

        Used by the inbound triage router to route a customer message to the right
        department: support (product help → Desk grounded answer), sales (buying signal →
        Intake lead), or it (technical/infrastructure issue → IT, also answered from docs).
        Default implementation: a lightweight keyword heuristic so triage degrades
        gracefully without an LLM. Real adapters (TFY gateway) override this with a
        fast-model classification. Ambiguous → ``"support"`` (always answer the question).
        """
        return _heuristic_classify_intent(text)


# ---------------------------------------------------------------------------
# Heuristic lead extraction (no-LLM fallback)
# ---------------------------------------------------------------------------

import re as _re  # noqa: E402 — local helper import, kept out of the public surface

_EMAIL_RE = _re.compile(r"[\w.+-]+@[\w-]+\.[\w-]+(?:\.[\w-]+)*")
_BUDGET_RE = _re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s?(?:k|m|thousand|million)?(?:\s?(?:/|per)\s?(?:yr|year|mo|month|seat))?",
    _re.IGNORECASE,
)
_AUTHORITY_RE = _re.compile(
    r"\b(?:VP|chief|cto|ceo|cfo|coo|director|head of|owner|founder|manager|decision[- ]maker)\b",
    _re.IGNORECASE,
)
_TIMELINE_RE = _re.compile(
    r"\b(?:this (?:quarter|month|week)|next (?:quarter|month)|q[1-4]|asap|immediately|"
    r"by (?:end of )?(?:the )?(?:quarter|month|year)|within \d+ (?:days|weeks|months))\b",
    _re.IGNORECASE,
)


def _heuristic_extract_lead(transcript: str) -> "LeadExtraction":
    text = transcript or ""
    email = _EMAIL_RE.search(text)
    budget = _BUDGET_RE.search(text)
    authority = _AUTHORITY_RE.search(text)
    timeline = _TIMELINE_RE.search(text)
    return LeadExtraction(
        email=email.group(0) if email else None,
        budget=budget.group(0).strip() if budget else None,
        authority=authority.group(0) if authority else None,
        timeline=timeline.group(0) if timeline else None,
    )


# ---------------------------------------------------------------------------
# Heuristic intent classification (no-LLM fallback)
# ---------------------------------------------------------------------------

# Department routing keywords. Priority: sales → it → support (default). "support" is the
# catch-all so we always answer a product question; "it" is reserved for clearly technical
# /infrastructure issues; "sales" is buying-signal language.
_SALES_RE = _re.compile(
    r"\b(?:pricing|price|quote|cost|how much|buy|purchase|demo|trial|"
    r"subscription|subscribe|plan|plans|upgrade|sales|interested in|"
    r"sign up|sign-up|signup|talk to sales|contact sales|enterprise|"
    r"discount|budget|procure|procurement|evaluat\w*)\b",
    _re.IGNORECASE,
)
# IT / technical-infrastructure signals (outages, auth, network, errors). Deliberately
# specific so ordinary product help ("how do I…", "my sync stopped") stays in support.
_IT_RE = _re.compile(
    r"\b(?:outage|server (?:down|error)|is down|are down|API (?:error|key|down)|"
    r"5\d\d error|error code|can'?t log ?in|cannot log ?in|locked out|password reset|"
    r"reset my password|2fa|mfa|sso|single sign[- ]on|vpn|firewall|network|dns|"
    r"certificate|ssl|deploy(?:ment)?|database (?:down|error)|webhook (?:fail\w*|down)|"
    r"security (?:issue|incident)|data breach|admin access|permission denied)\b",
    _re.IGNORECASE,
)


def _heuristic_classify_intent(text: str) -> "Literal['support', 'sales', 'it']":
    """Keyword heuristic. Priority: buying-signal → ``sales``; technical/infra → ``it``;
    otherwise ``support`` (so an ordinary product question is always answered)."""
    if not text:
        return "support"
    if _SALES_RE.search(text):
        return "sales"
    if _IT_RE.search(text):
        return "it"
    return "support"

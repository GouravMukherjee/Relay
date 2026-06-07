"""Intake-mode lead extraction + ICP scoring.

From a running call transcript, the LLM extracts the lead's identity + BANT qualifiers
(Budget / Authority / Need / Timeline). :func:`score_lead` turns those into an ICP fit
score (0–100) and a ``hot | warm | cold`` status. :func:`extract_and_store` ties it
together: extract → score → upsert the session's single ``Lead`` row → emit ``lead.update``.

There is exactly one Lead per Intake session; re-running on a longer transcript updates
the same row (qualifiers accumulate — a later turn never blanks an earlier qualifier).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select

from relay.db.base import privileged_session
from relay.db.models import Lead as LeadModel
from relay.ids import new_id
from relay.interfaces.llm import LeadExtraction, LLMClient
from relay.logging import get_logger
from relay.schemas.leads import lead_to_schema

logger = get_logger(__name__)

EmitFn = Callable[[str, dict[str, Any]], Awaitable[None]]

# Per-qualifier ICP weight (sums to 100 when all four are present).
_WEIGHTS = {"budget": 25, "authority": 20, "need": 35, "timeline": 20}


def score_lead(extraction: LeadExtraction) -> tuple[int, str]:
    """Return an ICP fit ``(score, status)`` from the extracted qualifiers.

    Score is the sum of the weights of the present BANT qualifiers (need is weighted
    highest — a clear, urgent problem is the strongest buying signal). Status:
    ``hot`` ≥ 70, ``warm`` ≥ 40, else ``cold``.
    """
    score = 0
    for field, weight in _WEIGHTS.items():
        if getattr(extraction, field, None):
            score += weight
    score = max(0, min(100, score))
    status = "hot" if score >= 70 else "warm" if score >= 40 else "cold"
    return score, status


def _qualifiers(extraction: LeadExtraction) -> dict[str, str]:
    """The non-empty BANT qualifiers as a plain dict (for the Lead.qualifiers JSON)."""
    out: dict[str, str] = {}
    for field in ("budget", "authority", "need", "timeline"):
        val = getattr(extraction, field, None)
        if val:
            out[field] = val
    return out


def _merge_qualifiers(existing: dict[str, Any], fresh: dict[str, str]) -> dict[str, str]:
    """Accumulate qualifiers — fresh values win, but a missing fresh field keeps the old."""
    merged = dict(existing or {})
    merged.update(fresh)
    return merged


async def extract_and_store(
    *,
    session_id: str,
    org_id: str,
    transcript: str,
    llm: LLMClient,
    emit: EmitFn | None = None,
) -> dict[str, Any] | None:
    """Extract → score → upsert the session's Lead → emit ``lead.update``.

    Returns the lead payload (the broadcast ``data``) or ``None`` if nothing could be
    extracted yet (no qualifiers and no identity — too early to show a lead card).
    """
    extraction = await llm.extract_lead(transcript=transcript)
    quals = _qualifiers(extraction)

    # Nothing to show yet — don't create an empty lead card.
    if not quals and not (extraction.name or extraction.company or extraction.email):
        return None

    score, status = score_lead(extraction)

    async with privileged_session() as db:
        result = await db.execute(
            select(LeadModel).where(LeadModel.session_id == session_id)
        )
        lead = result.scalar_one_or_none()

        if lead is None:
            lead = LeadModel(
                id=new_id("lead"),
                session_id=session_id,
                organization_id=org_id,
                name=extraction.name or "Unknown caller",
                company=extraction.company or "—",
                email=extraction.email or "—",
                qualifiers=quals,
                score=score,
                status=status,
                routed_to=None,
            )
            db.add(lead)
        else:
            # Fill identity only when newly discovered; never overwrite with a placeholder.
            # The name updates LIVE: a later turn that finally surfaces the caller's name
            # replaces the "Unknown caller" placeholder (or any earlier value), so the rep's
            # lead card flips from "Unknown caller" to the real name the instant it appears.
            if extraction.name and extraction.name != lead.name:
                lead.name = extraction.name
            if extraction.company:
                lead.company = extraction.company
            if extraction.email:
                lead.email = extraction.email
            lead.qualifiers = _merge_qualifiers(lead.qualifiers, quals)
            # Re-score from the accumulated qualifier set so the score only ever climbs
            # as more is learned.
            merged_extraction = LeadExtraction(**{k: lead.qualifiers.get(k) for k in _WEIGHTS})
            lead.score, lead.status = score_lead(merged_extraction)

        await db.flush()
        payload = lead_to_schema(lead).model_dump()

    logger.info(
        "intake.lead_update",
        extra={"session_id": session_id, "score": payload["score"], "status": payload["status"]},
    )
    if emit is not None:
        await emit("lead.update", payload)
    return payload


__all__ = ["score_lead", "extract_and_store"]

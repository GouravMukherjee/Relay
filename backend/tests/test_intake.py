"""Intake-mode lead extraction + ICP scoring tests (no external creds)."""
from __future__ import annotations

import pytest

from relay.interfaces.llm import LeadExtraction, _heuristic_extract_lead
from relay.orchestrator.intake import score_lead


def test_heuristic_extracts_bant_fields():
    transcript = (
        "Hi, I'm the VP of Engineering at Brightwave. You can reach me at "
        "vp.eng@brightwave.io. We've got a budget of about $50k/year and need to "
        "fix our onboarding latency this quarter."
    )
    ext = _heuristic_extract_lead(transcript)
    assert ext.email == "vp.eng@brightwave.io"
    assert ext.budget and "50" in ext.budget
    assert ext.authority and "VP" in ext.authority
    assert ext.timeline and "quarter" in ext.timeline.lower()


def test_score_hot_when_all_qualifiers_present():
    ext = LeadExtraction(
        budget="$50k/yr", authority="VP of Eng", need="reduce onboarding latency", timeline="this quarter"
    )
    score, status = score_lead(ext)
    assert score == 100
    assert status == "hot"


def test_score_cold_when_only_one_weak_signal():
    ext = LeadExtraction(timeline="next month")
    score, status = score_lead(ext)
    assert score == 20
    assert status == "cold"


def test_score_warm_in_middle_band():
    # need (35) + budget (25) = 60 -> warm
    ext = LeadExtraction(budget="$10k", need="cut support costs")
    score, status = score_lead(ext)
    assert score == 60
    assert status == "warm"


def test_empty_transcript_yields_empty_extraction():
    ext = _heuristic_extract_lead("")
    assert ext.budget is None and ext.email is None
    score, status = score_lead(ext)
    assert score == 0 and status == "cold"

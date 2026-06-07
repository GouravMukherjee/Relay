"""Relay entity ID generation.

All application entity PKs are prefixed text strings of the form::

    <prefix>_<24 hex characters from uuid4>

e.g. ``doc_4a3f2e1c9b8d7a6f5e4c3b2a``

The two exceptions to this scheme are:
  - ``organizations.id`` — plain UUID (not prefixed)
  - ``users.id``         — plain UUID (Supabase auth ``sub``, not prefixed)

Usage::

    from relay.ids import new_id

    document_id  = new_id("doc")   # "doc_..."
    chunk_id     = new_id("chk")   # "chk_..."
    session_id   = new_id("ses")   # "ses_..."
    utterance_id = new_id("utt")   # "utt_..."
    card_id      = new_id("card")  # "card_..."
    lead_id      = new_id("lead")  # "lead_..."
    memory_id    = new_id("mem")   # "mem_..."
    customer_id  = new_id("cus")   # "cus_..."
    notif_id     = new_id("ntf")   # "ntf_..."
"""

from __future__ import annotations

from uuid import uuid4

# ---------------------------------------------------------------------------
# Canonical prefix constants (import these to avoid typos in callers)
# ---------------------------------------------------------------------------

PREFIX_DOCUMENT = "doc"
PREFIX_CHUNK = "chk"
PREFIX_SESSION = "ses"
PREFIX_UTTERANCE = "utt"
PREFIX_CARD = "card"
PREFIX_LEAD = "lead"
PREFIX_MEMORY = "mem"
PREFIX_CUSTOMER = "cus"
PREFIX_NOTIFICATION = "ntf"


def new_id(prefix: str) -> str:
    """Return a new prefixed entity ID.

    Format: ``{prefix}_{uuid4_hex[:24]}``

    The 24 hex characters provide ~96 bits of randomness — sufficient for
    collision-resistance at any realistic scale.

    Args:
        prefix: Short entity-type label, e.g. ``"doc"``, ``"chk"``.

    Returns:
        A string like ``"doc_4a3f2e1c9b8d7a6f5e4c3b2a"``.
    """
    return f"{prefix}_{uuid4().hex[:24]}"

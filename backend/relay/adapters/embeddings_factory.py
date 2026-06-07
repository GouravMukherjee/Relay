"""Embeddings provider selection.

Prefers Qwen/DashScope (``QWEN_API_KEY``) — verified 1024-d, used to populate the
pgvector fallback — and falls back to the TFY embeddings adapter when Qwen is not
configured. Moss is the primary retrieval path and embeds server-side, so embeddings
here are only for the pgvector resilience layer + ingestion vectors.
"""
from __future__ import annotations

from relay.config import settings
from relay.interfaces.embeddings import Embeddings


def get_embeddings() -> Embeddings:
    """Return the configured embeddings adapter (Qwen preferred, else TFY)."""
    if settings.qwen_api_key:
        from relay.adapters.embeddings_qwen import QwenEmbeddings

        return QwenEmbeddings()
    from relay.adapters.embeddings_tfy import TfyEmbeddings

    return TfyEmbeddings()

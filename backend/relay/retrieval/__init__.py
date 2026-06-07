"""Composite retrieval — Moss (primary) with a pgvector fallback.

The live path queries this service only (never raw files). See
``relay/retrieval/service.py`` for :class:`CompositeRetrievalService`.
"""
from __future__ import annotations

from relay.retrieval.service import CompositeRetrievalService

__all__ = ["CompositeRetrievalService"]

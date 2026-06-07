"""Embeddings interface."""
from __future__ import annotations

from abc import ABC, abstractmethod


class Embeddings(ABC):
    """Abstract embeddings provider — TrueFoundry-hosted model in production.

    All implementations must produce 1024-dimensional float vectors so that
    pgvector's ``vector(1024)`` columns and the ivfflat index are consistent
    across adapters.
    """

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts.

        Args:
            texts: List of strings to embed (non-empty).

        Returns:
            List of float vectors in the same order as *texts*.
            Each vector must have exactly 1024 dimensions.
        """
        ...

"""TrueFoundry embeddings adapter.

Implements ``Embeddings`` (1024-d) via the TrueFoundry AI Gateway's
OpenAI-compatible embeddings endpoint.

Required creds: ``tfy_api_key``, ``tfy_gateway_url``.

# TODO: confirm <TrueFoundry> API — the embeddings endpoint path and model
# identifier below are best-guesses from TFY docs; verify before shipping.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from relay.config import settings
from relay.interfaces.embeddings import Embeddings

logger = logging.getLogger(__name__)

# Expected output dimension.  Must match ``settings.embedding_dim`` (1024).
_EXPECTED_DIM = 1024

# TODO: confirm <TrueFoundry> API — embedding model identifier on the gateway.
_DEFAULT_EMBEDDING_MODEL = "text-embedding-3-large"


class TfyEmbeddings(Embeddings):
    """Embeddings via the TrueFoundry AI Gateway (OpenAI-compatible endpoint).

    Produces 1024-dimensional float vectors compatible with pgvector
    ``vector(1024)`` columns and the ivfflat index.

    Required settings
    -----------------
    tfy_api_key     : str — Bearer token for the TFY gateway
    tfy_gateway_url : str — Gateway base URL (OpenAI-compatible)
    """

    def __init__(self) -> None:
        if not settings.tfy_api_key:
            raise RuntimeError(
                "TfyEmbeddings requires TFY_API_KEY to be set in the environment."
            )
        if not settings.tfy_gateway_url:
            raise RuntimeError(
                "TfyEmbeddings requires TFY_GATEWAY_URL to be set in the environment."
            )

        # Normalise base URL — strip trailing slash for httpx.
        base_url = settings.tfy_gateway_url.rstrip("/")

        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {settings.tfy_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0),
        )

    # ------------------------------------------------------------------
    # Embeddings interface
    # ------------------------------------------------------------------

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed *texts* and return a list of 1024-d float vectors.

        # TODO: confirm <TrueFoundry> API — verify /embeddings path and
        # response shape. Using OpenAI-compatible format assumed.
        """
        if not texts:
            return []

        payload: dict[str, Any] = {
            "input": texts,
            # TODO: confirm <TrueFoundry> API — correct model identifier for 1024-d.
            "model": _DEFAULT_EMBEDDING_MODEL,
            # Request 1024-d output where the API supports dimensionality reduction.
            "dimensions": _EXPECTED_DIM,
        }

        # TODO: confirm <TrueFoundry> API — endpoint path for embeddings.
        response = await self._client.post("/embeddings", json=payload)
        response.raise_for_status()
        data = response.json()

        # OpenAI-compatible response: {"data": [{"embedding": [...], "index": N}, ...]}
        # TODO: confirm <TrueFoundry> API — exact response schema.
        raw_data: list[dict[str, Any]] = data.get("data", [])
        # Sort by index to guarantee ordering matches the input list.
        raw_data.sort(key=lambda item: item.get("index", 0))

        vectors: list[list[float]] = []
        for item in raw_data:
            vec: list[float] = item.get("embedding", [])
            if len(vec) != _EXPECTED_DIM:
                logger.warning(
                    "tfy_embedding_dim_mismatch",
                    extra={"expected": _EXPECTED_DIM, "got": len(vec)},
                )
            vectors.append(vec)

        logger.debug(
            "tfy_embed_ok",
            extra={"text_count": len(texts), "dim": _EXPECTED_DIM},
        )
        return vectors

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Call on application shutdown."""
        await self._client.aclose()

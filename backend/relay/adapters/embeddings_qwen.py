"""Qwen / Alibaba DashScope embeddings adapter.

Implements ``Embeddings`` (1024-d) via the DashScope OpenAI-compatible endpoint
(``text-embedding-v3``). Used to populate the pgvector fallback index — Moss is the
primary path (built-in embeddings), so this is the resilience layer.

Required creds: ``qwen_api_key`` (DashScope key). Base URL: ``qwen_base_url``.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from relay.config import settings
from relay.interfaces.embeddings import Embeddings

logger = logging.getLogger(__name__)

_EXPECTED_DIM = 1024


class QwenEmbeddings(Embeddings):
    """Embeddings via DashScope (OpenAI-compatible ``/embeddings``)."""

    def __init__(self) -> None:
        if not settings.qwen_api_key:
            raise RuntimeError("QwenEmbeddings requires QWEN_API_KEY to be set.")
        self._model = settings.qwen_embedding_model
        self._client = httpx.AsyncClient(
            base_url=settings.qwen_base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {settings.qwen_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0),
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, Any] = {
            "model": self._model,
            "input": texts,
            "dimensions": _EXPECTED_DIM,
            "encoding_format": "float",
        }
        resp = await self._client.post("/embeddings", json=payload)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        data.sort(key=lambda item: item.get("index", 0))
        return [item.get("embedding", []) for item in data]

    async def aclose(self) -> None:
        await self._client.aclose()

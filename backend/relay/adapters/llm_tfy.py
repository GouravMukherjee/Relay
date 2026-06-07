"""TrueFoundry LLM adapter.

Implements ``LLMClient`` via the TrueFoundry AI Gateway's OpenAI-compatible
``/chat/completions`` endpoint. The model is a provider-prefixed gateway id
(``settings.tfy_model``, e.g. ``anthropic/claude-sonnet-4-5`` for Claude, or a
Qwen/Minimax id) — TFY routes and bills it. No direct provider SDK/key is used:
Claude is reached THROUGH TrueFoundry, so only ``tfy_api_key`` is required.

Required creds: ``tfy_api_key``, ``tfy_gateway_url``.

Grounding contract
------------------
The LLM is instructed to answer ONLY from the provided chunks and to cite
them. If the chunks do not contain a relevant answer, the model must return
the sentinel value ``"__NO_CARD__"`` and this adapter returns ``None``.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from relay.config import settings
from relay.interfaces.llm import CardDraft, LLMClient
from relay.interfaces.retrieval import RetrievedChunk

logger = logging.getLogger(__name__)

# Sentinel that the model should output when it cannot ground an answer.
_NO_CARD_SENTINEL = "__NO_CARD__"

# TODO: confirm <TrueFoundry> API — model identifiers on the TFY gateway.
_MODEL_IDS: dict[str, str] = {
    "claude": "claude-sonnet-4-5",  # primary; routed through TFY → Anthropic
    "qwen": "qwen-plus",            # TODO: confirm <TrueFoundry> API — Qwen model ID
    "minimax": "abab6.5-chat",      # TODO: confirm <TrueFoundry> API — Minimax model ID
}

# Maximum answer length in characters (kept short for voice/card display).
_MAX_ANSWER_CHARS = 600

_SYSTEM_PROMPT = """\
You are Relay, an AI co-pilot that answers questions strictly from the
provided document excerpts. Your grounding rules are absolute:

1. Answer ONLY from the excerpts below. Do not use outside knowledge.
2. Keep the answer to 1–3 sentences, clear and direct.
3. End your response with a JSON block (fenced as ```json) that lists the
   chunk IDs you actually cited, like:
   ```json
   {"cited_chunks": ["chk_abc123", "chk_def456"]}
   ```
4. If the provided excerpts do not contain a relevant answer, respond with
   exactly: __NO_CARD__
   Do not add any other text if you use the sentinel.
5. Do not speculate, fabricate, or fill gaps from general knowledge.
""".replace("__NO_CARD__", _NO_CARD_SENTINEL)


def _build_user_message(
    query: str,
    chunks: list[RetrievedChunk],
    mode: str,
    window: list[str] | None,
) -> str:
    """Assemble the user-turn message with grounding material."""
    lines: list[str] = []

    if window:
        lines.append("## Recent conversation context (do NOT use as grounding source)")
        lines.extend(f"  {line}" for line in window[-10:])  # cap at last 10 lines
        lines.append("")

    lines.append(f"## Mode: {mode}")
    lines.append("")
    lines.append("## Document excerpts (your ONLY allowed source material)")
    lines.append("")
    for i, chunk in enumerate(chunks, start=1):
        lines.append(
            f"[{i}] chunk_id={chunk.chunk_id!r} | doc={chunk.document_id!r} | title={chunk.title!r}"
        )
        lines.append(chunk.text)
        lines.append("")

    lines.append(f"## Question")
    lines.append(query)

    return "\n".join(lines)


def _parse_response(raw: str) -> CardDraft | None:
    """Extract answer and cited chunk IDs from the model's raw text."""
    raw = raw.strip()

    # Sentinel check — model declines to ground an answer.
    if raw == _NO_CARD_SENTINEL or raw.startswith(_NO_CARD_SENTINEL):
        return None

    # Extract the JSON citation block if present.
    cited_ids: list[str] = []
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if json_match:
        try:
            citation_data: dict[str, Any] = json.loads(json_match.group(1))
            cited_ids = citation_data.get("cited_chunks", [])
        except json.JSONDecodeError:
            logger.warning("llm_citation_json_parse_error", extra={"raw": raw[:200]})
        # Strip the citation block from the display answer.
        answer = raw[: json_match.start()].strip()
    else:
        answer = raw

    if not answer:
        return None

    # Truncate to max display length.
    if len(answer) > _MAX_ANSWER_CHARS:
        answer = answer[:_MAX_ANSWER_CHARS].rstrip() + "…"

    return CardDraft(answer=answer, title=None, used_chunk_ids=cited_ids)


class TfyLLMClient(LLMClient):
    """LLM client routed through the TrueFoundry AI Gateway.

    Routes based on ``settings.llm_model``:
      - ``"claude"``  → Anthropic SDK with ``base_url`` pointed at TFY
      - ``"qwen"``    → httpx against TFY (OpenAI-compatible)
      - ``"minimax"`` → httpx against TFY (OpenAI-compatible)

    Required settings
    -----------------
    tfy_api_key      : str — Bearer token for the gateway
    tfy_gateway_url  : str — Gateway base URL
    anthropic_api_key: str — Required only for the ``"claude"`` path
    """

    def __init__(self) -> None:
        if not settings.tfy_api_key:
            raise RuntimeError(
                "TfyLLMClient requires TFY_API_KEY to be set in the environment."
            )
        if not settings.tfy_gateway_url:
            raise RuntimeError(
                "TfyLLMClient requires TFY_GATEWAY_URL to be set in the environment."
            )

        self._model_name = settings.llm_model  # label only: "claude" | "qwen" | "minimax"
        # Provider-prefixed gateway model id (verified working via /chat/completions),
        # e.g. "anthropic/claude-sonnet-4-5". TFY rejects unprefixed ids.
        self._model_id = settings.tfy_model or _MODEL_IDS.get(
            self._model_name, "anthropic/claude-sonnet-4-5"
        )
        self._gateway_url = settings.tfy_gateway_url.rstrip("/")

        # All models route through the OpenAI-compatible /chat/completions endpoint on
        # the TFY gateway (confirmed against the live gateway). The Anthropic-SDK path
        # was removed: TFY exposes /chat/completions, not the Anthropic /v1/messages path.
        self._http_client = httpx.AsyncClient(
            base_url=self._gateway_url,
            headers={
                "Authorization": f"Bearer {settings.tfy_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0),
        )

    # ------------------------------------------------------------------
    # LLMClient interface
    # ------------------------------------------------------------------

    async def synthesize_card(
        self,
        *,
        query: str,
        chunks: list[RetrievedChunk],
        mode: str,
        window: list[str] | None = None,
    ) -> CardDraft | None:
        """Synthesise a grounded answer from *chunks*.

        Returns ``None`` if the model cannot ground an answer ("no card").
        """
        if not chunks:
            return None

        user_message = _build_user_message(query, chunks, mode, window)
        return await self._synthesize_openai_compat(user_message)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _synthesize_openai_compat(self, user_message: str) -> CardDraft | None:
        """Call the chat model via the TFY OpenAI-compatible /chat/completions endpoint."""
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": 512,
            "temperature": 0.0,
        }

        # TODO: confirm <TrueFoundry> API — chat completions endpoint path.
        response = await self._http_client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()

        raw_text: str = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        logger.debug(
            "tfy_llm_openai_compat_ok",
            extra={"model": self._model_id},
        )
        return _parse_response(raw_text)

    async def aclose(self) -> None:
        """Close underlying HTTP clients. Call on application shutdown."""
        await self._http_client.aclose()

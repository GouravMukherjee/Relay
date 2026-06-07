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
from collections.abc import AsyncIterator
from typing import Any

import httpx

from relay.config import settings
from relay.interfaces.llm import (
    CardDraft,
    CardStreamEvent,
    LeadExtraction,
    LLMClient,
    _heuristic_classify_intent,
    _heuristic_extract_lead,
)
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
_MAX_ANSWER_CHARS = 320
# Desk replies are customer-ready messages (a few sentences) — allow more room.
_DESK_MAX_ANSWER_CHARS = 800


def _max_chars_for(mode: str) -> int:
    """Display-answer char cap for *mode* (Desk replies run longer than live cards)."""
    return _DESK_MAX_ANSWER_CHARS if mode == "desk" else _MAX_ANSWER_CHARS

_SYSTEM_PROMPT = """\
You are Relay, a live co-pilot. Answer the question in ONE or at most TWO short
sentences, strictly from the provided document excerpts. Be direct — no preamble,
no "according to the document". These rules are absolute:

1. Answer ONLY from the excerpts below. Never use outside knowledge.
2. 1–2 sentences. Lead with the answer. No bullet lists, no headings.
3. End your response with a fenced ```json block listing the chunk IDs you cited:
   ```json
   {"cited_chunks": ["chk_abc123"]}
   ```
4. If the excerpts contain no relevant answer, respond with exactly: __NO_CARD__
   and nothing else.
5. Never speculate, fabricate, or fill gaps from general knowledge.
""".replace("__NO_CARD__", _NO_CARD_SENTINEL)


# Desk mode produces a polished, customer-READY reply: first person, addressed directly to
# the customer, warm and empathetic — but STILL strictly grounded in (and cited from) the
# provided excerpts. The rep sends this near-verbatim, so it must read as a finished message,
# not a terse co-pilot hint. Grounding + citation rules are identical and absolute.
_DESK_SYSTEM_PROMPT = """\
You are a friendly customer-support agent writing a reply the customer will read directly.
Write in the FIRST PERSON, addressed to the customer ("you"), warm and empathetic — a
polished, ready-to-send message of 2–4 short sentences. Acknowledge their concern briefly,
then give the answer. These rules are absolute:

1. Answer ONLY from the excerpts below. Never use outside knowledge.
2. Write a complete, customer-ready reply (greeting optional; no "according to the
   document", no internal notes). Be specific and reassuring, never robotic.
3. End your response with a fenced ```json block listing the chunk IDs you cited:
   ```json
   {"cited_chunks": ["chk_abc123"]}
   ```
4. If the excerpts contain no relevant answer, respond with exactly: __NO_CARD__
   and nothing else.
5. Never speculate, fabricate, promise, or fill gaps from general knowledge.
""".replace("__NO_CARD__", _NO_CARD_SENTINEL)


def _system_prompt_for(mode: str) -> str:
    """Pick the card system prompt for *mode*.

    ``desk`` → the customer-ready, empathetic first-person prompt (the suggested reply the
    rep sends to the customer). ``live``/``intake`` → the terse co-pilot prompt (a glanceable
    hint for the operator). Both enforce the identical grounding + citation contract.
    """
    return _DESK_SYSTEM_PROMPT if mode == "desk" else _SYSTEM_PROMPT


def _clean(value: Any) -> str | None:
    """Normalise an extracted field: strip, and treat empty/null-ish as None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("null", "none", "n/a", "unknown", "-"):
        return None
    return s


def _strip_citation_block(raw: str) -> str:
    """Return *raw* with any (possibly partial/streaming) trailing ```json citation
    block removed, so the running answer can be displayed cleanly mid-stream.

    During streaming the closing fence may not have arrived yet, so we also drop a
    dangling ```json / ``` opener at the tail.
    """
    # Complete fenced block.
    m = re.search(r"```json\s*\{.*?\}\s*```", raw, re.DOTALL)
    if m:
        return raw[: m.start()].rstrip()
    # Partial block still streaming in — cut at the opening fence.
    idx = raw.rfind("```")
    if idx != -1 and "json" in raw[idx : idx + 8].lower():
        return raw[:idx].rstrip()
    if idx != -1 and raw[idx:].strip() in ("```", "```json"):
        return raw[:idx].rstrip()
    return raw


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


def _parse_response(raw: str, max_chars: int = _MAX_ANSWER_CHARS) -> CardDraft | None:
    """Extract answer and cited chunk IDs from the model's raw text.

    ``max_chars`` caps the display answer; Desk replies are customer-ready paragraphs so
    the caller passes a larger budget than the terse live-card default.
    """
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
    if len(answer) > max_chars:
        answer = answer[:max_chars].rstrip() + "…"

    return CardDraft(answer=answer, title=None, used_chunk_ids=cited_ids)


class TfyLLMClient(LLMClient):
    """LLM client: TrueFoundry AI Gateway primary, direct Anthropic SDK fallback.

    Routes based on ``settings.llm_model``:
      - ``"claude"``  → TFY /chat/completions first; falls back to direct Anthropic SDK
      - ``"qwen"``    → TFY /chat/completions (OpenAI-compatible)
      - ``"minimax"`` → TFY /chat/completions (OpenAI-compatible)

    Required settings
    -----------------
    tfy_api_key      : str — Bearer token for the TFY gateway
    tfy_gateway_url  : str — Gateway base URL
    anthropic_api_key: str — Direct Anthropic API key (fallback when TFY billing fails)
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
        # Fast model used ONLY for live card synthesis (1–2 sentence cited answers).
        # Falls back to the primary model + configured fallbacks if it errors.
        self._fast_model_id = settings.tfy_fast_model or self._model_id
        self._card_max_tokens = max(32, int(settings.card_max_tokens or 150))
        self._gateway_url = settings.tfy_gateway_url.rstrip("/")

        # All models route through the OpenAI-compatible /chat/completions endpoint on
        # the TFY gateway (confirmed against the live gateway).
        self._http_client = httpx.AsyncClient(
            base_url=self._gateway_url,
            headers={
                "Authorization": f"Bearer {settings.tfy_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0),
        )

        # Direct Anthropic client — used when TFY billing fails (credit balance errors).
        self._anthropic_client = None
        if settings.anthropic_api_key:
            try:
                import anthropic as _anthropic
                self._anthropic_client = _anthropic.AsyncAnthropic(
                    api_key=settings.anthropic_api_key
                )
            except ImportError:
                pass

        # Qwen DashScope intl — last-resort fallback (free tier, works for synthesis).
        self._qwen_http_client = None
        if settings.qwen_api_key:
            self._qwen_http_client = httpx.AsyncClient(
                base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                headers={
                    "Authorization": f"Bearer {settings.qwen_api_key}",
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
        # Fast model first (cards are 1–2 sentences), then the primary model, then
        # configured fallbacks, then direct Anthropic.
        models = self._card_models()
        last_exc: Exception | None = None
        for model in models:
            try:
                return await self._synthesize_openai_compat(user_message, model, mode=mode)
            except Exception as exc:  # noqa: BLE001 — try the next model on any failure
                last_exc = exc
                logger.warning("llm model failed; trying fallback", extra={"model": model, "error": str(exc)})

        # All TFY paths failed — try direct Anthropic SDK if available (handles billing errors).
        if self._anthropic_client is not None:
            try:
                return await self._synthesize_anthropic_direct(user_message, mode=mode)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("anthropic direct fallback failed", extra={"error": str(exc)})

        # Last resort: Qwen DashScope (always available on free tier).
        if self._qwen_http_client is not None:
            try:
                return await self._synthesize_openai_compat(user_message, "qwen-plus", client=self._qwen_http_client, mode=mode)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("qwen fallback failed", extra={"error": str(exc)})

        if last_exc is not None:
            raise last_exc
        return None

    # ------------------------------------------------------------------
    # Streaming synthesis (token-by-token; primary live path)
    # ------------------------------------------------------------------

    async def synthesize_card_stream(
        self,
        *,
        query: str,
        chunks: list[RetrievedChunk],
        mode: str,
        window: list[str] | None = None,
    ) -> AsyncIterator[CardStreamEvent]:
        """Stream a grounded answer from the fast model token-by-token.

        Yields incremental display deltas (citation block stripped) while the model
        writes, then one terminal event carrying the parsed :class:`CardDraft` (or
        ``None`` for the grounding guard). On any streaming failure it falls back to
        the non-streaming path so a card is still produced.
        """
        if not chunks:
            yield CardStreamEvent(done=True, draft=None)
            return

        user_message = _build_user_message(query, chunks, mode, window)
        try:
            async for ev in self._stream_openai_compat(user_message, self._fast_model_id, mode):
                yield ev
            return
        except Exception as exc:  # noqa: BLE001 — fall back to non-streaming synthesis
            logger.warning(
                "card stream failed; falling back to non-streaming",
                extra={"model": self._fast_model_id, "error": str(exc)},
            )

        draft = await self.synthesize_card(
            query=query, chunks=chunks, mode=mode, window=window
        )
        if draft is not None and draft.answer:
            yield CardStreamEvent(delta=draft.answer)
        yield CardStreamEvent(done=True, draft=draft)

    async def _stream_openai_compat(
        self, user_message: str, model: str, mode: str = "live"
    ) -> AsyncIterator[CardStreamEvent]:
        """Stream one /chat/completions call (SSE), emitting clean display deltas.

        ``mode`` selects the system prompt (Desk = customer-ready) and the token/char
        budget (Desk answers run longer than terse live cards).
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": _system_prompt_for(mode)},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": self._max_tokens_for(mode),
            "temperature": 0.0,
            "stream": True,
        }

        raw = ""          # full accumulated raw text (answer + citation block)
        shown = ""        # display text already emitted as deltas
        sentinel_possible = True

        async with self._http_client.stream(
            "POST", "/chat/completions", json=payload
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[len("data:") :].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                delta = (
                    chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                )
                if not delta:
                    continue
                raw += delta

                # Grounding guard: if the answer begins with the no-card sentinel,
                # emit nothing — the card never appears.
                stripped = raw.lstrip()
                if sentinel_possible:
                    if stripped.startswith(_NO_CARD_SENTINEL):
                        continue
                    if _NO_CARD_SENTINEL.startswith(stripped):
                        # Still ambiguous (e.g. "__NO") — wait before showing anything.
                        continue
                    sentinel_possible = False

                display = _strip_citation_block(raw)
                if len(display) > len(shown):
                    new_text = display[len(shown) :]
                    shown = display
                    yield CardStreamEvent(delta=new_text)

        draft = _parse_response(raw, _max_chars_for(mode))
        logger.debug("llm_stream_ok", extra={"model": model})
        yield CardStreamEvent(done=True, draft=draft)

    # ------------------------------------------------------------------
    # Intake lead extraction
    # ------------------------------------------------------------------

    async def extract_lead(self, *, transcript: str) -> LeadExtraction:
        """Extract lead identity + BANT qualifiers from a transcript via the LLM.

        Returns a :class:`LeadExtraction`; falls back to the regex heuristic on any
        LLM/parse failure so Intake always produces *something* to score.
        """
        transcript = (transcript or "").strip()
        if not transcript:
            return LeadExtraction()

        system = (
            "You extract sales-lead fields from a call transcript. Output ONLY a JSON "
            "object with these keys: name, company, email, budget, authority, need, "
            "timeline. Use null for anything not clearly stated. 'authority' = the "
            "caller's decision-making role; 'need' = the problem they want solved; "
            "'budget' and 'timeline' are verbatim phrases. Do not invent values."
        )
        user = f"Transcript:\n{transcript}\n\nReturn the JSON object now."
        try:
            data = await self._extract_json(system, user, self._fast_model_id)
            return LeadExtraction(
                name=_clean(data.get("name")),
                company=_clean(data.get("company")),
                email=_clean(data.get("email")),
                budget=_clean(data.get("budget")),
                authority=_clean(data.get("authority")),
                need=_clean(data.get("need")),
                timeline=_clean(data.get("timeline")),
            )
        except Exception as exc:  # noqa: BLE001 — degrade to heuristic
            logger.warning("lead extraction failed; using heuristic", extra={"error": str(exc)})
            return _heuristic_extract_lead(transcript)

    async def classify_intent(self, *, text: str) -> str:
        """Classify a customer message as ``"support"``, ``"sales"``, or ``"it"`` via the
        fast model.

        Falls back to the keyword heuristic on any LLM/parse failure so inbound triage
        always routes. Ambiguous / unparseable → ``"support"`` (answer the question),
        matching the contract's default-support rule.
        """
        text = (text or "").strip()
        if not text:
            return "support"

        system = (
            "You are an intent router for an inbound customer message. Classify it as "
            "exactly one of three departments and respond with ONLY that single lowercase "
            "word, nothing else:\n"
            "- 'sales' — buying intent: pricing, quotes, demos, trials, plans, purchasing, "
            "upgrading, talking to sales.\n"
            "- 'it' — a technical or infrastructure problem: outages, the service is down, "
            "API/server errors, login/SSO/password/access issues, network, security "
            "incidents, integrations failing at a system level.\n"
            "- 'support' — general product help: how-to questions, account/billing, a "
            "feature not behaving, or anything that doesn't clearly fit the other two.\n"
            "If unsure, answer 'support'."
        )
        user = f"Message:\n{text}\n\nDepartment:"
        try:
            payload: dict[str, Any] = {
                "model": self._fast_model_id,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": 4,
                "temperature": 0.0,
            }
            response = await self._http_client.post("/chat/completions", json=payload)
            response.raise_for_status()
            content: str = (
                response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            )
            answer = content.strip().lower()
            if "sales" in answer:
                return "sales"
            if "it" == answer or answer.startswith("it") or "i.t" in answer:
                return "it"
            if "support" in answer:
                return "support"
            # Unrecognised output — fall back to the heuristic rather than guessing.
            return _heuristic_classify_intent(text)
        except Exception as exc:  # noqa: BLE001 — degrade to heuristic
            logger.warning("intent classify failed; using heuristic", extra={"error": str(exc)})
            return _heuristic_classify_intent(text)

    async def _extract_json(self, system: str, user: str, model: str) -> dict[str, Any]:
        """Call the gateway for a JSON object and parse it (tolerant of fences)."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 300,
            "temperature": 0.0,
        }
        response = await self._http_client.post("/chat/completions", json=payload)
        response.raise_for_status()
        content: str = (
            response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        )
        m = re.search(r"\{.*\}", content, re.DOTALL)
        return json.loads(m.group(0)) if m else {}

    # ------------------------------------------------------------------
    # Connection warm-up
    # ------------------------------------------------------------------

    async def prewarm(self) -> None:
        """Open the TLS/HTTP keep-alive connection to the gateway ahead of the
        first real query, so session start pays the handshake cost — not the
        latency-critical card path. Best-effort: never raises.
        """
        try:
            await self._http_client.post(
                "/chat/completions",
                json={
                    "model": self._fast_model_id,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "temperature": 0.0,
                },
            )
        except Exception as exc:  # noqa: BLE001 — warm-up is best-effort
            logger.debug("llm prewarm skipped", extra={"error": str(exc)})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _card_models(self) -> list[str]:
        """Model ids to try for card synthesis, in order: fast → primary → fallbacks.

        Deduplicated, order-preserving, so the fast model is attempted first but the
        primary model and any configured fallbacks still back it up.
        """
        candidates = [self._fast_model_id, self._model_id] + [
            m.strip() for m in settings.tfy_fallback_models.split(",") if m.strip()
        ]
        seen: set[str] = set()
        ordered: list[str] = []
        for m in candidates:
            if m and m not in seen:
                seen.add(m)
                ordered.append(m)
        return ordered

    def _max_tokens_for(self, mode: str) -> int:
        """Completion token budget for *mode* (Desk replies need more than live cards)."""
        if mode == "desk":
            return max(self._card_max_tokens, 400)
        return self._card_max_tokens

    async def _synthesize_openai_compat(
        self,
        user_message: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        mode: str = "live",
    ) -> CardDraft | None:
        """Call *model* via an OpenAI-compatible /chat/completions endpoint.

        ``mode`` selects the system prompt + token/char budget (Desk = customer-ready).
        """
        http = client or self._http_client
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": _system_prompt_for(mode)},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": self._max_tokens_for(mode),
            "temperature": 0.0,
        }

        response = await http.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()

        raw_text: str = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        logger.debug("llm_ok", extra={"model": model})
        return _parse_response(raw_text, _max_chars_for(mode))

    async def _synthesize_anthropic_direct(
        self, user_message: str, mode: str = "live"
    ) -> CardDraft | None:
        """Call the Anthropic API directly (fallback when TFY billing fails)."""
        # Anthropic's model ID doesn't use the provider prefix. This is the
        # billing-failure fallback (not the latency path), so use the known-good
        # primary model id rather than the gateway-only fast alias.
        model = self._model_id.replace("anthropic/", "")
        msg = await self._anthropic_client.messages.create(
            model=model,
            max_tokens=self._max_tokens_for(mode),
            system=_system_prompt_for(mode),
            messages=[{"role": "user", "content": user_message}],
        )
        raw_text: str = msg.content[0].text if msg.content else ""
        logger.debug("anthropic_direct_ok", extra={"model": model})
        return _parse_response(raw_text, _max_chars_for(mode))

    async def aclose(self) -> None:
        """Close underlying HTTP clients. Call on application shutdown."""
        await self._http_client.aclose()
        if self._qwen_http_client is not None:
            await self._qwen_http_client.aclose()

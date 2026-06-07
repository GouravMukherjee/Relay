"""Relay LiveKit agent worker.

Joins a LiveKit room, streams audio through LiveKit Inference STT, pushes
transcript events to the WsHub, runs TriggerDetector, and on fire calls the Orchestrator
then pushes ``card.new`` (with streaming ``card.update`` for long answers).

Run modes
---------
Development (local LiveKit)::

    python -m relay.agent.worker dev

Production (connect to configured LiveKit Cloud URL)::

    python -m relay.agent.worker start

Both modes are handled by the LiveKit Agents CLI via ``cli.run_app``.

Architecture invariants honoured
---------------------------------
- Live path never touches raw files — only the pre-built Moss/pgvector index.
- Grounding guard is enforced by Orchestrator (returns None on no-grounding).
- Tenant org_id comes from room metadata, NOT from participant messages.
- LiveKit tokens are minted server-side (see relay.adapters.livekit_tokens).
- Secrets sourced from relay.config.settings only.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# LiveKit Agents imports
# ---------------------------------------------------------------------------
# TODO: confirm LiveKit Agents API — the import paths below are correct for
# livekit-agents >=0.8.x (voice pipeline style).  If the installed version
# differs, adjust the import paths accordingly.
try:
    from livekit.agents import JobContext, WorkerOptions, cli
    from livekit.agents.voice import Agent, AgentSession  # type: ignore[import-untyped]
except ImportError as exc:
    raise ImportError(
        "livekit-agents must be installed. Run: pip install 'livekit-agents>=0.8.0'"
    ) from exc

# ---------------------------------------------------------------------------
# Relay imports
# ---------------------------------------------------------------------------
from relay.agent.trigger import TriggerDetector
from relay.config import settings
from relay.ids import new_id, stable_session_id
from relay.schemas.common import build_event

logger = logging.getLogger(__name__)

# Sentence-initial interrogative words — used to make sure a closing detector never
# swallows a real question ("what's the price, thanks" still leads with "what").
_INTERROGATIVE_LEAD_RE = re.compile(
    r"^\s*(?:who|what|whats|what'?s|when|where|why|how|which|whose|whom|can|could|would|"
    r"will|should|shall|do|does|did|is|are|was|were|have|has|had|may|might|tell me|"
    r"i (?:have|had|need|want|wanted)|could you|can you|do you)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Lazy imports — only resolved at call time to avoid circular imports and to
# keep worker startup fast even when adapters are not yet fully wired.
# ---------------------------------------------------------------------------


def _get_hub() -> Any:
    """Return the WsHub singleton from the gateway.

    Imported lazily so the agent worker module can be imported without the
    FastAPI gateway being fully initialised (e.g. in tests).
    """
    try:
        from relay.gateway.ws import hub  # type: ignore[import-untyped]
        return hub
    except ImportError:
        logger.warning("relay.gateway.ws not found — WS broadcast disabled")
        return None


def _get_orchestrator_class() -> Any:
    """Return the Orchestrator class, lazily."""
    try:
        from relay.orchestrator.synth import Orchestrator  # type: ignore[import-untyped]
        return Orchestrator
    except ImportError:
        logger.warning("relay.orchestrator.synth not found — card synthesis disabled")
        return None


def _get_composite_retrieval() -> Any:
    """Build a CompositeRetrievalService if adapters are available."""
    try:
        from relay.retrieval.service import CompositeRetrievalService  # type: ignore[import-untyped]
        return CompositeRetrievalService.from_settings()
    except Exception as exc:
        logger.warning("Could not build CompositeRetrievalService: %s", exc)
        return None


def _get_llm_client() -> Any:
    """Build the TfyLLMClient if the adapter is available."""
    try:
        from relay.adapters.llm_tfy import TfyLLMClient  # type: ignore[import-untyped]
        return TfyLLMClient()
    except Exception as exc:
        logger.warning("Could not build TfyLLMClient: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


async def _ensure_session_row(session_id: str, org_id: str, mode: str, room: str) -> None:
    """Create the Session row for a deterministic (demo-room) session if absent.

    Cards persist with a ``session_id`` FK, so the row must exist before the first
    card is written. Idempotent — a no-op if the row already exists. Best-effort:
    failures are logged, never fatal (the live path still broadcasts cards even if
    persistence can't happen).
    """
    try:
        from datetime import datetime as _dt, timezone as _tz

        from relay.db.base import privileged_session
        from relay.db.models import Session as _Session

        async with privileged_session() as db:
            existing = await db.get(_Session, session_id)
            if existing is not None:
                return
            db.add(
                _Session(
                    id=session_id,
                    organization_id=org_id,
                    mode=mode,
                    livekit_room=room,
                    status="active",
                    started_at=_dt.now(tz=_tz.utc),
                )
            )
            await db.commit()
            logger.info("agent: ensured demo session row session=%s", session_id)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("agent: could not ensure session row %s: %s", session_id, exc)


def _endpointing_turn_handling() -> Any | None:
    """Build TurnHandlingOptions with a tightened endpointing delay, or None if the
    installed livekit-agents version doesn't expose it.
    """
    try:
        from livekit.agents import TurnHandlingOptions  # type: ignore

        return TurnHandlingOptions(
            endpointing={
                "mode": "fixed",
                "min_delay": settings.stt_min_endpointing_delay,
                "max_delay": settings.stt_max_endpointing_delay,
            },
        )
    except Exception as exc:  # noqa: BLE001 — optional / version-dependent
        logger.info("agent: TurnHandlingOptions unavailable (%s)", exc)
        return None


def _build_agent_session(stt_model: str | None) -> Any:
    """Construct the AgentSession with tuned endpointing + TTS, degrading gracefully.

    Two independent fallback ladders are composed here:

    - STT/endpointing: (STT + turn_handling) -> (STT only) -> (no STT).
    - TTS: each successful branch is first attempted *with* TTS so the Live agent
      has a voice. TTS itself has its own ladder — primary model
      (``settings.livekit_tts_model``) -> fallback model
      (``settings.livekit_tts_fallback_model``) -> no TTS (current behaviour).

    The worker always starts: a bad TTS model never blocks STT, and a bad STT
    model never blocks startup.
    """
    turn_handling = _endpointing_turn_handling()

    def _build(**kwargs: Any) -> Any:
        """Try AgentSession(**kwargs, tts=primary) -> (tts=fallback) -> (no tts).

        Returns the session on the first success, logging which TTS (if any) is
        active. Re-raises only if even the no-TTS construction fails, so the
        caller's STT ladder can degrade further.
        """
        primary = settings.livekit_tts_model
        fallback = settings.livekit_tts_fallback_model
        for tts_model in (primary, fallback):
            if not tts_model:
                continue
            try:
                session = AgentSession(tts=tts_model, **kwargs)  # type: ignore[attr-defined]
                logger.info("agent: TTS enabled", extra={"tts_model": tts_model})
                return session
            except Exception as exc:  # noqa: BLE001 — version/model may reject tts
                logger.warning(
                    "agent: AgentSession with tts=%r failed: %s — trying next TTS option",
                    tts_model,
                    exc,
                )
        # No TTS — caller never hears a voice, but transcription still works.
        session = AgentSession(**kwargs)  # type: ignore[attr-defined]
        logger.info("agent: TTS disabled (no working TTS model)")
        return session

    # 1) Preferred: STT + tightened endpointing.
    if stt_model and turn_handling is not None:
        try:
            session = _build(stt=stt_model, turn_handling=turn_handling)
            logger.info(
                "agent: STT + endpointing enabled",
                extra={
                    "model": stt_model,
                    "min_delay": settings.stt_min_endpointing_delay,
                    "max_delay": settings.stt_max_endpointing_delay,
                },
            )
            return session
        except Exception as exc:  # noqa: BLE001 — version may not accept turn_handling
            logger.warning("agent: AgentSession(stt, turn_handling) failed: %s — retrying", exc)

    # 2) STT only.
    if stt_model:
        try:
            session = _build(stt=stt_model)
            logger.info("agent: STT enabled (default endpointing)", extra={"model": stt_model})
            return session
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "agent: failed to create AgentSession with STT model %r: %s — falling back to no STT",
                stt_model,
                exc,
            )

    # 3) No STT (still runs; manual queries work).
    logger.info("agent: STT disabled")
    return _build()


def _parse_room_metadata(metadata: str | None) -> dict[str, str]:
    """Parse room metadata JSON into a plain dict.

    Returns an empty dict on any parse failure so callers can always call
    ``.get("key", default)`` safely.
    """
    import json

    if not metadata:
        return {}
    try:
        parsed = json.loads(metadata)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Per-session relay agent
# ---------------------------------------------------------------------------


class RelayAgent:
    """Encapsulates the per-session state for one LiveKit room.

    One instance is created per call to ``entrypoint()``.  It wires together:
    - LiveKit Inference STT (partial + final transcripts)
    - TriggerDetector (question detection + debounced continuous)
    - Orchestrator (retrieval → synthesis → card)
    - WsHub broadcast (transcript.partial, transcript.final, card.new, card.update)
    """

    def __init__(
        self,
        ctx: JobContext,
        session_id: str,
        org_id: str,
        mode: str,
        customer_id: str | None,
    ) -> None:
        self._ctx = ctx
        self._session_id = session_id
        self._org_id = org_id
        self._mode = mode
        self._customer_id = customer_id

        self._trigger = TriggerDetector(
            continuous_interval_s=15.0,
            dedup_window=8,
        )
        self._retrieval_backend: str = "moss"
        self._synthesis_lock: asyncio.Lock = asyncio.Lock()
        # Intake-mode state: rolling transcript + a lock so extractions don't overlap.
        self._intake_parts: list[str] = []
        self._intake_lock: asyncio.Lock = asyncio.Lock()
        self._hub = _get_hub()
        # The AgentSession reference is wired in by entrypoint() after the session is
        # built (see ``relay_agent._session_obj = session``). It lets the Live agent
        # SPEAK card answers / clarifying lines via the configured TTS. None until set,
        # and every use is guarded so TTS can never break the card pipeline.
        self._session_obj: Any | None = None
        # Greet-once guard + lock: the agent speaks a customer-service greeting the moment
        # the first caller joins a Live room (never an empty room), exactly once.
        self._greeted: bool = False
        self._greet_lock: asyncio.Lock = asyncio.Lock()
        # The Orchestrator needs a DB session (to persist Card/CardSource), so it is
        # built per-synthesis inside a privileged_session — see _synthesise_and_broadcast.

    # ------------------------------------------------------------------
    # Event handlers (called from AgentSession event callbacks)
    # ------------------------------------------------------------------

    def on_partial_transcript(self, text: str, speaker: str = "participant") -> None:
        """Handle a STT partial transcript — broadcast and update trigger state."""
        self._trigger.on_partial(text)
        self._broadcast(
            build_event(
                "transcript.partial",
                {"speaker": speaker, "text": text},
                _utcnow_iso(),
            )
        )

    def on_final_transcript(self, text: str, speaker: str = "participant") -> None:
        """Handle a STT final transcript — persist utterance, broadcast, maybe fire."""
        utterance_id = new_id("utt")
        ts = _utcnow_iso()

        # Broadcast final transcript to dashboard.
        self._broadcast(
            build_event(
                "transcript.final",
                {
                    "utterance_id": utterance_id,
                    "speaker": speaker,
                    "text": text,
                },
                ts,
            )
        )

        # Persist utterance to DB (fire-and-forget; don't block the audio path).
        asyncio.create_task(
            self._persist_utterance(utterance_id, speaker, text, ts),
            name=f"persist_utt_{utterance_id}",
        )

        # Intake mode: every turn is lead signal — accumulate + re-extract/score the
        # lead instead of synthesising a grounded card.
        if self._mode == "intake":
            self._intake_parts.append(text)
            asyncio.create_task(
                self._extract_and_broadcast_lead(),
                name=f"intake_{self._session_id}_{utterance_id}",
            )
            return

        # Live mode: a closing/acknowledgement ("got it, thanks") is NOT a new question.
        # Speak a brief customer-service close and clear the rolling window so the
        # debounced continuous trigger can't replay the previous answer (the loop bug).
        if self._mode == "live" and self._is_closing(text):
            logger.info("session=%s: closing detected %r — speaking sign-off", self._session_id, text[:60])
            self._trigger.clear_window()
            asyncio.create_task(
                self._say(self._closing_line(text)),
                name=f"close_{self._session_id}_{utterance_id}",
            )
            return

        # Live / Desk: trigger detection -> grounded card synthesis.
        query_text = self._trigger.should_fire(text, speaker=speaker)
        if query_text:
            asyncio.create_task(
                self._synthesise_and_broadcast(query_text),
                name=f"synth_{self._session_id}_{utterance_id}",
            )

    # ------------------------------------------------------------------
    # Voice (Live mode only)
    # ------------------------------------------------------------------

    # Canned, fact-free steering lines spoken when the orchestrator returns no
    # card (no grounding). They keep the caller engaged toward the call's topic
    # WITHOUT inventing any company facts. Chosen deterministically by query
    # length (see _no_card_line) so behaviour is stable, not random.
    _NO_CARD_LINES: tuple[str, ...] = (
        "I want to make sure I get you the right information — "
        "could you give me a little more detail on that?",
        "Good question — let me make sure I point you to the right answer. "
        "Could you clarify what you're looking for?",
        "I want to be sure I help you with exactly the right thing — "
        "can you tell me a bit more about what you need?",
    )

    def _no_card_line(self, query_text: str) -> str:
        """Pick a no-card steering line deterministically (by query length)."""
        return self._NO_CARD_LINES[len(query_text) % len(self._NO_CARD_LINES)]

    # Closing / acknowledgement phrases. When the caller says one of these (e.g. "got it,
    # thanks"), they are NOT asking a new question — re-running retrieval would replay the
    # previous answer (the "looping" bug). Instead we say a brief customer-service close.
    _CLOSING_RE = re.compile(
        r"\b(?:thanks|thank you|thank u|thank\s*you\s*so\s*much|got it|that'?s (?:all|it|"
        r"great|perfect|helpful|everything|what i needed|exactly what i needed)|"
        r"that (?:answers|covers) (?:it|my question|everything)|appreciate it|appreciated|"
        r"perfect|great|awesome|wonderful|excellent|sounds good|will do|"
        r"no(?:pe)?(?:,?\s*(?:that'?s all|i'?m good|thank))?|i'?m (?:good|all set|set)|"
        r"all set|that helps|that helped|helpful|bye|good\s*bye|see you|see ya|cheers|"
        r"have a (?:good|great)|take care|talk (?:to you )?later|okay thanks|ok thanks)\b",
        re.IGNORECASE,
    )
    # Strong closing words that are decisive even in a longer sentence.
    _STRONG_CLOSING_RE = re.compile(
        r"\b(?:thank you|thanks|appreciate it|that'?s all|that'?s everything|i'?m all set|"
        r"all set|good\s*bye|\bbye\b|take care|have a (?:good|great)|that (?:answers|covers))\b",
        re.IGNORECASE,
    )
    _BYE_RE = re.compile(
        r"\b(?:bye|good\s*bye|see you|see ya|that'?s all|that'?s everything|have a (?:good|great)|take care)\b",
        re.IGNORECASE,
    )

    def _is_closing(self, text: str) -> bool:
        """True if *text* is an acknowledgement / sign-off (not a new question).

        Tolerant of natural phone phrasing: a short utterance matching common closing
        words counts, and even a longer sentence counts if it carries a STRONG closing
        signal (e.g. "thank you", "that's all", "bye") and isn't itself a question.
        """
        t = (text or "").strip()
        if not t:
            return False
        # A real question (mark, or starts with an interrogative) is never a closing.
        if "?" in t or _INTERROGATIVE_LEAD_RE.match(t):
            return False
        # Short utterances: any closing keyword is enough.
        if len(t) <= 80 and self._CLOSING_RE.search(t):
            return True
        # Longer utterances: require a STRONG closing signal so we don't mistake a
        # real request that happens to contain "great" for a sign-off.
        if len(t) <= 160 and self._STRONG_CLOSING_RE.search(t):
            return True
        return False

    def _closing_line(self, text: str) -> str:
        """A warm, customer-service closing reply for an acknowledgement."""
        if self._BYE_RE.search(text or ""):
            return "Thanks for calling Northwind. Have a great day!"
        return "You're welcome! Is there anything else I can help you with?"

    async def greet(self) -> None:
        """Speak the customer-service greeting exactly once, when a caller is present.

        Triggered by participant-join (NOT by a timer), so it never greets an empty demo
        room. A lock + the ``_greeted`` flag guarantee a single greeting even if several
        join events fire: the first call acquires the lock and greets (retrying internally
        until the AgentSession's TTS is ready); concurrent calls block on the lock, then
        see ``_greeted`` and return. Best-effort — the call still works if TTS never comes up.
        """
        if self._mode != "live" or self._greeted:
            return
        greeting = (settings.agent_greeting or "").strip()
        if not greeting:
            return
        async with self._greet_lock:
            if self._greeted:
                return  # another join event already greeted while we waited on the lock
            # Retry a few times: a join event can beat the TTS pipeline being ready.
            for _ in range(6):
                session = self._session_obj
                if session is not None:
                    try:
                        await session.say(greeting)
                        self._greeted = True
                        logger.info("session=%s: greeted caller", self._session_id)
                        return
                    except Exception as exc:  # noqa: BLE001 — not ready yet; retry
                        logger.debug("session=%s: greet not ready (%s)", self._session_id, exc)
                await asyncio.sleep(0.5)
            logger.warning("session=%s: greeting could not be delivered", self._session_id)

    async def _say(self, text: str) -> None:
        """Speak *text* through the AgentSession's TTS, if available.

        Live mode only. Fully guarded: a missing session, missing/failed TTS, or
        any version difference in ``say`` is logged at debug and swallowed so the
        card pipeline is never affected.
        """
        if self._mode != "live":
            return
        session = self._session_obj
        if session is None:
            logger.debug("session=%s: no AgentSession to speak with", self._session_id)
            return
        text = (text or "").strip()
        if not text:
            return
        try:
            await session.say(text)
        except Exception as exc:  # noqa: BLE001 — TTS must never break the pipeline
            logger.debug("session=%s: say() failed: %s", self._session_id, exc)

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------

    async def _synthesise_and_broadcast(self, query_text: str) -> None:
        """Call the Orchestrator and push card.new (with streaming card.update).

        Uses a per-session lock so only one synthesis is in-flight at a time.
        If a second trigger fires while a synthesis is already running, it is
        dropped — the live-path latency budget takes priority over queuing.
        """
        if self._synthesis_lock.locked():
            logger.debug(
                "session=%s: synthesis already in-flight, dropping query %r",
                self._session_id,
                query_text[:60],
            )
            return

        async with self._synthesis_lock:
            logger.info(
                "session=%s: synthesising card for query %r",
                self._session_id,
                query_text[:80],
            )
            try:
                t_start = asyncio.get_event_loop().time()

                # Build the orchestrator per call inside a privileged DB session (mirrors
                # the manual-query path in relay.gateway.ws). Reuses the tolerant builder
                # so adapter/cred issues surface as a clean error rather than crashing.
                from relay.db.base import privileged_session
                from relay.gateway.ws import _build_orchestrator, _OrchestratorUnavailable

                # Stream tokens straight to the dashboard as they arrive (ordered).
                async def _emit(event_type: str, data: dict[str, Any]) -> None:
                    if self._hub is not None:
                        await self._hub.broadcast(
                            self._session_id,
                            build_event(event_type, data, _utcnow_iso()),
                        )

                try:
                    async with privileged_session() as db:
                        orchestrator = _build_orchestrator(db)
                        card = await orchestrator.synthesize(
                            session_id=self._session_id,
                            org_id=self._org_id,
                            mode=self._mode,
                            query_text=query_text,
                            customer_id=self._customer_id,
                            emit=_emit,
                        )
                except _OrchestratorUnavailable as exc:
                    logger.warning(
                        "session=%s: orchestrator unavailable: %s", self._session_id, exc
                    )
                    return

                if card is None:
                    # Grounding guard: no relevant chunk — no card is broadcast (we
                    # never invent facts). But in Live mode, instead of dead silence,
                    # speak a brief, fact-free steering line so the caller stays engaged.
                    logger.info(
                        "session=%s: no grounding found for query %r — no card",
                        self._session_id,
                        query_text[:80],
                    )
                    await self._say(self._no_card_line(query_text))
                    # Drop the window so the continuous trigger doesn't replay this turn.
                    self._trigger.clear_window()
                    return

                elapsed_ms = int((asyncio.get_event_loop().time() - t_start) * 1000)
                logger.info(
                    "session=%s: card synthesised in %dms card_id=%s",
                    self._session_id,
                    elapsed_ms,
                    card.card_id,
                )

                # The card was already streamed to the dashboard via _emit
                # (card.new + card.update). No second broadcast needed.

                # Live mode: speak the grounded answer so the caller hears it. This
                # runs AFTER the broadcast (the lock is already held for this
                # synthesis), so the dashboard card is never blocked on TTS. Guarded
                # inside _say — TTS failures never break the card pipeline.
                await self._say(getattr(card, "answer", "") or "")

                # Drop the rolling window now that this turn is answered — otherwise the
                # debounced continuous trigger would re-fire the same window (still holding
                # this question) and the agent would "loop" the answer when the caller
                # pauses. Dedup history is preserved by clear_window().
                self._trigger.clear_window()

                # Update retrieval backend status on first card.
                # (The Orchestrator sets card.retrieval_backend if available.)
                backend = getattr(card, "retrieval_backend", None)
                if backend and backend != self._retrieval_backend:
                    self._retrieval_backend = backend
                    self._broadcast(
                        build_event(
                            "session.status",
                            {
                                "status": "active",
                                "retrieval_backend": self._retrieval_backend,
                            },
                            _utcnow_iso(),
                        )
                    )

            except Exception as exc:
                logger.exception(
                    "session=%s: synthesis error: %s", self._session_id, exc
                )
                self._broadcast(
                    build_event(
                        "error",
                        {"code": "internal_error", "message": str(exc)},
                        _utcnow_iso(),
                    )
                )

    # ------------------------------------------------------------------
    # Intake lead extraction
    # ------------------------------------------------------------------

    async def _extract_and_broadcast_lead(self) -> None:
        """Re-extract + score the lead from the accumulated Intake transcript.

        Coalesces overlapping turns via a lock (the latest transcript wins) and
        broadcasts ``lead.update`` on success. Best-effort: failures are logged.
        """
        if self._intake_lock.locked():
            # An extraction is in-flight; it'll pick up the latest transcript on the
            # next turn. Dropping here keeps us from queuing redundant LLM calls.
            return
        async with self._intake_lock:
            try:
                from relay.gateway.ws import get_llm_client
                from relay.orchestrator.intake import extract_and_store

                async def _emit(event_type: str, data: dict) -> None:
                    if self._hub is not None:
                        await self._hub.broadcast(
                            self._session_id,
                            build_event(event_type, data, _utcnow_iso()),
                        )

                await extract_and_store(
                    session_id=self._session_id,
                    org_id=self._org_id,
                    transcript=" ".join(self._intake_parts[-40:]),
                    llm=get_llm_client(),
                    emit=_emit,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "session=%s: intake extraction failed: %s", self._session_id, exc
                )

    # ------------------------------------------------------------------
    # Persistence helpers (fire-and-forget)
    # ------------------------------------------------------------------

    async def _persist_utterance(
        self, utterance_id: str, speaker: str, text: str, ts: str
    ) -> None:
        """Write an Utterance row to the database.

        This is best-effort — failures are logged and swallowed so they never
        block the audio path.
        """
        try:
            from relay.db.base import async_session_maker  # type: ignore[import-untyped]
            from relay.db.models import Utterance  # type: ignore[import-untyped]
            from datetime import datetime as _dt, timezone as _tz

            created_at = _dt.now(tz=_tz.utc)

            async with async_session_maker() as db:
                utt = Utterance(
                    id=utterance_id,
                    session_id=self._session_id,
                    organization_id=uuid.UUID(self._org_id),
                    speaker=speaker,
                    text=text,
                    ts=created_at,
                )
                db.add(utt)
                await db.commit()
        except Exception as exc:
            logger.warning(
                "session=%s: failed to persist utterance %s: %s",
                self._session_id,
                utterance_id,
                exc,
            )

    # ------------------------------------------------------------------
    # WS broadcast helper
    # ------------------------------------------------------------------

    def _broadcast(self, event: dict) -> None:
        """Fire-and-forget broadcast to the WsHub for this session."""
        if self._hub is None:
            return
        asyncio.create_task(
            self._hub.broadcast(self._session_id, event),
            name=f"ws_broadcast_{self._session_id}",
        )


# ---------------------------------------------------------------------------
# LiveKit Agents entrypoint
# ---------------------------------------------------------------------------


async def entrypoint(ctx: JobContext) -> None:  # noqa: C901
    """LiveKit Agents entrypoint — called once per dispatched room job.

    Reads session metadata from the room metadata JSON (keys: session_id,
    org_id, mode, customer_id).  Falls back to safe defaults if metadata is
    absent so the worker can be tested without a fully wired gateway.

    Room metadata JSON shape (set by routes/sessions.py when creating the room)::

        {
          "session_id": "ses_...",
          "org_id": "00000000-...",
          "mode": "live",
          "customer_id": "cus_..."   // optional
        }
    """
    await ctx.connect()

    # Enable the Redis-backed hub so cards/transcripts broadcast from THIS (agent)
    # process reach browser sockets registered on the gateway process.
    hub = _get_hub()
    if hub is not None:
        try:
            await hub.start_redis()
        except Exception as exc:  # noqa: BLE001 — degrade to local-only (won't reach browser)
            logger.warning("agent: hub.start_redis failed: %s", exc)

    # Warm the LLM gateway + retrieval adapters now (session start) so the first
    # fired trigger doesn't pay connection/handshake cost on the live path.
    try:
        from relay.gateway.ws import prewarm_llm

        asyncio.create_task(prewarm_llm())
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug("agent: prewarm skipped: %s", exc)

    # ------------------------------------------------------------------
    # Parse session context. Metadata may arrive on the ROOM (gateway ensure_room)
    # or as explicit-dispatch JOB metadata (gateway/SIP dispatch). Merge both — job
    # metadata wins where present.
    # ------------------------------------------------------------------
    meta = _parse_room_metadata(getattr(ctx.room, "metadata", None))
    job_meta = _parse_room_metadata(getattr(getattr(ctx, "job", None), "metadata", None))
    if job_meta:
        meta = {**meta, **{k: v for k, v in job_meta.items() if v}}

    room_name = ctx.room.name or ""
    is_demo_room = bool(settings.livekit_demo_room) and room_name == settings.livekit_demo_room

    if is_demo_room and not meta.get("session_id"):
        # Inbound-phone demo room: no per-session metadata was stamped (e.g. a raw SIP
        # dispatch). Use the DETERMINISTIC demo session id so cards land on the exact WS
        # channel the rep dashboard watches, and default to a Live session for the org.
        session_id = stable_session_id(room_name)
        org_id = meta.get("org_id") or settings.default_org_id
        mode = "live"
        customer_id = None
        # Ensure the demo Session row exists before any card is persisted (FK target).
        await _ensure_session_row(session_id, org_id, mode, room_name)
    else:
        session_id = meta.get("session_id") or new_id("ses")
        org_id = meta.get("org_id") or settings.default_org_id
        mode = meta.get("mode") or "live"
        customer_id = meta.get("customer_id") or None

    logger.info(
        "agent: joining room=%s session=%s org=%s mode=%s demo=%s",
        room_name,
        session_id,
        org_id,
        mode,
        is_demo_room,
    )

    # ------------------------------------------------------------------
    # Build per-session relay agent
    # ------------------------------------------------------------------
    relay_agent = RelayAgent(
        ctx=ctx,
        session_id=session_id,
        org_id=org_id,
        mode=mode,
        customer_id=customer_id,
    )

    # ------------------------------------------------------------------
    # LiveKit AgentSession + LiveKit Inference STT
    # ------------------------------------------------------------------
    # STT runs through LiveKit Inference: a model string (e.g.
    # "assemblyai/universal-streaming") is passed to AgentSession and routed by
    # LiveKit using the existing LIVEKIT_API_KEY / LIVEKIT_API_SECRET — no separate
    # STT provider account or plugin package, billed against LiveKit credits. The
    # model is configurable via settings.livekit_stt_model (env LIVEKIT_STT_MODEL).
    # TODO: confirm LiveKit Agents API — AgentSession(stt="<provider>/<model>") is the
    # LiveKit Inference form for livekit-agents >=1.x; adjust if the installed version differs.

    # The Agent is STT-only — no LLM or TTS; Relay's Orchestrator handles synthesis.
    agent = Agent(  # type: ignore[attr-defined]
        instructions="You are a silent transcription agent.",  # unused (no LLM), but required by Agent
    )

    # STT via LiveKit Inference (model string); partial transcripts enabled by default.
    # Endpointing is tuned for low perceived latency: a short trailing-silence delay so
    # finals fire quickly after the speaker stops (LiveKit's default min_delay of 0.5s is
    # additive on top of the STT's own endpointing). Built tolerantly: if the installed
    # livekit-agents version doesn't accept turn_handling, we retry without it, and if
    # the STT model can't be created we fall back to a session without STT.
    stt_model = settings.livekit_stt_model
    session = _build_agent_session(stt_model)

    # Give the relay agent a handle on the live AgentSession so it can SPEAK
    # (card answers + no-card steering lines) via the configured TTS. Guarded at
    # the point of use (RelayAgent._say) — None-safe and try/excepted.
    relay_agent._session_obj = session

    # ------------------------------------------------------------------
    # Wire transcript events → RelayAgent handlers
    # ------------------------------------------------------------------

    @session.on("user_input_transcribed")  # type: ignore[misc]
    def _on_transcribed(transcript_event: Any) -> None:  # noqa: ANN001
        """Called by AgentSession for each STT transcript chunk.

        The transcript_event object provides:
          - transcript_event.transcript: str  — the text
          - transcript_event.is_final: bool   — True = final, False = partial

        Speaker attribution: LiveKit Agents does not expose the speaker
        identity directly in this callback.  We use "participant" as a
        stable default; a future enhancement could inspect ctx.room
        .remote_participants to find the active publisher.
        """
        # TODO: confirm LiveKit Agents API — field names on the transcript event
        # object may differ across versions (e.g. .text vs .transcript).
        text: str = getattr(transcript_event, "transcript", "") or getattr(
            transcript_event, "text", ""
        )
        is_final: bool = bool(getattr(transcript_event, "is_final", False))

        if not text:
            return

        speaker = _resolve_speaker(ctx)

        if is_final:
            relay_agent.on_final_transcript(text, speaker=speaker)
        else:
            relay_agent.on_partial_transcript(text, speaker=speaker)

    # ------------------------------------------------------------------
    # Inbound-call indicator: tell the dashboard when a SIP caller joins/leaves.
    # A SIP participant is just another audio participant for transcription — this
    # only drives the "Incoming call" UI; it does NOT special-case the STT pipeline.
    # ------------------------------------------------------------------
    def _broadcast_call_status(active: bool, kind: str, identity: str = "") -> None:
        if hub is None:
            return
        asyncio.create_task(
            hub.broadcast(
                session_id,
                build_event(
                    "session.status",
                    {
                        "status": "active",
                        "retrieval_backend": "moss",
                        "call_active": active,
                        "call_kind": kind,        # "sip" | "browser"
                        "caller": identity,
                    },
                    _utcnow_iso(),
                ),
            )
        )

    def _maybe_greet() -> None:
        """Greet the caller once when they join a Live room (greet() is single-flighted)."""
        if mode == "live":
            asyncio.create_task(relay_agent.greet(), name=f"greet_{session_id}")

    def _on_participant_connected(participant: Any) -> None:  # noqa: ANN001
        if _is_sip_participant(participant):
            logger.info("agent: SIP caller joined session=%s id=%s", session_id, getattr(participant, "identity", ""))
            _broadcast_call_status(True, "sip", str(getattr(participant, "identity", "")))
        # Greet on join (the caller is now present). greet() is lock-guarded + once-only,
        # so repeated join events never double-speak, and it never greets an empty room.
        _maybe_greet()

    def _on_participant_disconnected(participant: Any) -> None:  # noqa: ANN001
        if _is_sip_participant(participant):
            logger.info("agent: SIP caller left session=%s", session_id)
            _broadcast_call_status(False, "sip", str(getattr(participant, "identity", "")))

    try:
        ctx.room.on("participant_connected", _on_participant_connected)
        ctx.room.on("participant_disconnected", _on_participant_disconnected)
    except Exception as exc:  # noqa: BLE001 — event API differences are non-fatal
        logger.debug("agent: could not wire participant events: %s", exc)

    # A SIP caller may already be in the room when the agent joins (dispatch races the
    # call setup) — surface the indicator for any pre-existing SIP participant.
    try:
        existing = list(getattr(ctx.room, "remote_participants", {}).values())
        for p in existing:
            if _is_sip_participant(p):
                _broadcast_call_status(True, "sip", str(getattr(p, "identity", "")))
                break
        # If a caller is already present when the agent joins, greet them (once-guarded).
        if existing:
            _maybe_greet()
    except Exception:  # noqa: BLE001
        pass

    # ------------------------------------------------------------------
    # Emit initial session.status to connected dashboard clients
    # ------------------------------------------------------------------
    if hub is not None:
        asyncio.create_task(
            hub.broadcast(
                session_id,
                build_event(
                    "session.status",
                    {"status": "active", "retrieval_backend": "moss"},
                    _utcnow_iso(),
                ),
            )
        )

    # NOTE: the greeting is driven by participant-join (above), NOT by a post-start timer —
    # a timer would greet the empty demo room before the caller dials in, consuming the
    # one-shot. greet() retries internally until TTS is ready, so a join always lands it.

    # ------------------------------------------------------------------
    # Start the session — blocks until the room closes.
    # Start the session — blocks until the room closes.
    # BVC noise cancellation requires a LiveKit Cloud paid add-on; attempting
    # to use it on a project without the feature enabled causes a Rust-level
    # WebRTC panic and crashes the worker. Disabled until the Cloud feature is
    # provisioned on relay-ayfm1fbo.livekit.cloud.
    #
    # close_on_disconnect=False: keep the session alive across participant churn.
    # By default the session tears down on every participant disconnect, and that
    # teardown path triggers a native webrtc-sys panic ("malformed serialized
    # RtcError") under the rapid join/leave we see while testing. Keeping the session
    # alive means teardown runs once (at room end) instead of on every leave, so the
    # panic is rare rather than constant. Built tolerantly: if the installed
    # livekit-agents version rejects the kwarg, fall back to a bare start.
    # ------------------------------------------------------------------
    try:
        from livekit.agents import RoomInputOptions  # type: ignore

        await session.start(
            agent=agent,
            room=ctx.room,
            room_input_options=RoomInputOptions(close_on_disconnect=False),
        )
    except TypeError:
        # Older/newer signature without room_input_options — start without it.
        await session.start(agent=agent, room=ctx.room)
    except ImportError:
        await session.start(agent=agent, room=ctx.room)

    logger.info(
        "agent: session ended session=%s room=%s",
        session_id,
        ctx.room.name,
    )


def _is_sip_participant(participant: Any) -> bool:
    """True if *participant* is an inbound SIP (phone) caller.

    Checks the participant ``kind`` against the SIP enum when available, and falls
    back to the conventional ``sip_`` identity prefix LiveKit assigns SIP callers —
    so detection works across livekit-rtc versions without a hard enum dependency.
    """
    try:
        from livekit import rtc  # type: ignore

        sip_kind = getattr(rtc.ParticipantKind, "PARTICIPANT_KIND_SIP", None)
        if sip_kind is not None and getattr(participant, "kind", None) == sip_kind:
            return True
    except Exception:  # noqa: BLE001
        pass
    identity = str(getattr(participant, "identity", "") or "")
    return identity.startswith("sip_") or identity.startswith("sip-")


# ---------------------------------------------------------------------------
# Helper: resolve speaker identity from the room
# ---------------------------------------------------------------------------


def _resolve_speaker(ctx: JobContext) -> str:
    """Return a speaker label for the active remote participant.

    Falls back to "participant" if the room has no remote participants yet.
    """
    # TODO: confirm LiveKit Agents API — ctx.room.remote_participants may be
    # a dict[str, RemoteParticipant] or similar depending on livekit version.
    try:
        participants = list(ctx.room.remote_participants.values())
        if participants:
            return str(participants[0].identity or "participant")
    except Exception:
        pass
    return "participant"


# ---------------------------------------------------------------------------
# Worker entry point
# ---------------------------------------------------------------------------

# Exposed for import by tests or by ``python -m relay.agent.worker``.
# Pass LiveKit connection from settings (loaded from .env by pydantic-settings) so the
# worker doesn't depend on the vars being exported into the process environment.
# agent_name registers an explicit DISPATCH name. With it set, the worker is assigned to
# a room ONLY when explicitly dispatched (by the gateway for browser sessions, or by a
# SIP dispatch rule for inbound phone calls) — never auto-dispatched to every room.
worker_options = WorkerOptions(
    entrypoint_fnc=entrypoint,
    agent_name=settings.livekit_agent_name or None,
    ws_url=settings.livekit_url or None,
    api_key=settings.livekit_api_key or None,
    api_secret=settings.livekit_api_secret or None,
)

if __name__ == "__main__":
    # Usage:
    #   python -m relay.agent.worker dev    — local LiveKit dev server
    #   python -m relay.agent.worker start  — connect to settings.livekit_url
    cli.run_app(worker_options)

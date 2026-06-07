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
from relay.ids import new_id
from relay.schemas.common import build_event

logger = logging.getLogger(__name__)

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
        self._hub = _get_hub()
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

        # Trigger detection.
        query_text = self._trigger.should_fire(text, speaker=speaker)
        if query_text:
            asyncio.create_task(
                self._synthesise_and_broadcast(query_text),
                name=f"synth_{self._session_id}_{utterance_id}",
            )

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

                try:
                    async with privileged_session() as db:
                        orchestrator = _build_orchestrator(db)
                        card = await orchestrator.synthesize(
                            session_id=self._session_id,
                            org_id=self._org_id,
                            mode=self._mode,
                            query_text=query_text,
                            customer_id=self._customer_id,
                        )
                except _OrchestratorUnavailable as exc:
                    logger.warning(
                        "session=%s: orchestrator unavailable: %s", self._session_id, exc
                    )
                    return

                if card is None:
                    # Grounding guard: no relevant chunk — stay silent.
                    logger.info(
                        "session=%s: no grounding found for query %r — no card",
                        self._session_id,
                        query_text[:80],
                    )
                    return

                elapsed_ms = int((asyncio.get_event_loop().time() - t_start) * 1000)
                logger.info(
                    "session=%s: card synthesised in %dms card_id=%s",
                    self._session_id,
                    elapsed_ms,
                    card.card_id,
                )

                # Push card.new.
                card_dict = card.model_dump() if hasattr(card, "model_dump") else dict(card)
                self._broadcast(
                    build_event("card.new", card_dict, _utcnow_iso())
                )

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

    # ------------------------------------------------------------------
    # Parse room metadata → session context
    # ------------------------------------------------------------------
    meta = _parse_room_metadata(getattr(ctx.room, "metadata", None))

    session_id: str = meta.get("session_id") or new_id("ses")
    org_id: str = meta.get("org_id") or settings.default_org_id
    mode: str = meta.get("mode") or "live"
    customer_id: str | None = meta.get("customer_id") or None

    logger.info(
        "agent: joining room=%s session=%s org=%s mode=%s",
        ctx.room.name,
        session_id,
        org_id,
        mode,
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
    session = AgentSession(stt=settings.livekit_stt_model)  # type: ignore[attr-defined]

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
    # Emit initial session.status to connected dashboard clients
    # ------------------------------------------------------------------
    hub = _get_hub()
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

    # ------------------------------------------------------------------
    # Start the session — blocks until the room closes.
    # Enable LiveKit Cloud enhanced noise + background-voice cancellation when the
    # plugin is available (improves STT/turn-detection quality on noisy mics). Guarded
    # so the worker runs without the optional dependency.
    # ------------------------------------------------------------------
    start_kwargs: dict[str, Any] = {"agent": agent, "room": ctx.room}
    try:
        from livekit.agents import RoomInputOptions  # type: ignore
        from livekit.plugins import noise_cancellation  # type: ignore

        start_kwargs["room_input_options"] = RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC()
        )
        logger.info("agent: BVC noise cancellation enabled")
    except Exception as exc:  # noqa: BLE001 — optional; LiveKit Cloud only
        logger.info("agent: noise cancellation unavailable (%s)", exc)

    await session.start(**start_kwargs)

    logger.info(
        "agent: session ended session=%s room=%s",
        session_id,
        ctx.room.name,
    )


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
worker_options = WorkerOptions(
    entrypoint_fnc=entrypoint,
    ws_url=settings.livekit_url or None,
    api_key=settings.livekit_api_key or None,
    api_secret=settings.livekit_api_secret or None,
)

if __name__ == "__main__":
    # Usage:
    #   python -m relay.agent.worker dev    — local LiveKit dev server
    #   python -m relay.agent.worker start  — connect to settings.livekit_url
    cli.run_app(worker_options)

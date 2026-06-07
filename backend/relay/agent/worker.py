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
from relay.ids import new_id, stable_session_id
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
    """Construct the AgentSession with tuned endpointing, degrading gracefully.

    Tries (STT + turn_handling) -> (STT only) -> (no STT) so the worker always starts.
    """
    turn_handling = _endpointing_turn_handling()

    # 1) Preferred: STT + tightened endpointing.
    if stt_model and turn_handling is not None:
        try:
            session = AgentSession(stt=stt_model, turn_handling=turn_handling)  # type: ignore[attr-defined]
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
            session = AgentSession(stt=stt_model)  # type: ignore[attr-defined]
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
    return AgentSession()  # type: ignore[attr-defined]


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

        # Live / Desk: trigger detection -> grounded card synthesis.
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

                # The card was already streamed to the dashboard via _emit
                # (card.new + card.update). No second broadcast needed.

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

    def _on_participant_connected(participant: Any) -> None:  # noqa: ANN001
        if _is_sip_participant(participant):
            logger.info("agent: SIP caller joined session=%s id=%s", session_id, getattr(participant, "identity", ""))
            _broadcast_call_status(True, "sip", str(getattr(participant, "identity", "")))

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
        for p in list(getattr(ctx.room, "remote_participants", {}).values()):
            if _is_sip_participant(p):
                _broadcast_call_status(True, "sip", str(getattr(p, "identity", "")))
                break
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

    # ------------------------------------------------------------------
    # Start the session — blocks until the room closes.
    # Start the session — blocks until the room closes.
    # BVC noise cancellation requires a LiveKit Cloud paid add-on; attempting
    # to use it on a project without the feature enabled causes a Rust-level
    # WebRTC panic and crashes the worker. Disabled until the Cloud feature is
    # provisioned on relay-ayfm1fbo.livekit.cloud.
    # ------------------------------------------------------------------
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

"""WebSocket gateway: the per-session event hub and the ``/ws/sessions/{id}`` route.

Responsibilities
----------------
* :class:`WsHub` — a process-singleton registry mapping ``session_id`` to the set of
  connected sockets, with ``register`` / ``unregister`` / ``broadcast`` primitives. The
  agent worker, the REST query path, and this router all push events through it.
* The ``/ws/sessions/{session_id}`` route — authenticates the handshake via the
  ``?token=`` query param (Supabase JWT), enforces the configured frontend origin,
  registers the socket with the hub, emits an initial ``session.status``, then pumps the
  inbound client event stream (``mode.set`` / ``query.manual`` / ``card.pin`` /
  ``card.dismiss``). ``query.manual`` runs the grounding-guarded Orchestrator and, on a
  grounded answer, broadcasts a ``card.new``; a "no-card" result is silently dropped
  (grounded-or-silent — never a fabricated card, never an error to the client).

Event envelopes are always ``{"type", "ts", "data"}`` built via
:func:`relay.schemas.common.build_event`; ``ts`` is stamped here at emit time (this is the
boundary that owns wall-clock time — the helper itself never calls ``datetime.now``).

Server -> client events emitted/relayed here: ``transcript.partial``, ``transcript.final``,
``card.new``, ``card.update``, ``session.status``, ``lead.update``, ``error``.
Client -> server events handled here: ``mode.set``, ``query.manual``, ``card.pin``,
``card.dismiss``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

# Exceptions that all mean "the client went away" — a browser tab closing/navigating or a
# socket dropped without a clean close frame. They are NORMAL, not errors: we treat them as
# a clean disconnect so they never log a traceback or trip the error watcher.
_DISCONNECT_EXCS: tuple[type[BaseException], ...] = (WebSocketDisconnect,)
try:  # websockets raises ConnectionClosed(Error/OK) on an abrupt drop (no close frame)
    from websockets.exceptions import ConnectionClosed as _WsConnectionClosed

    _DISCONNECT_EXCS = (*_DISCONNECT_EXCS, _WsConnectionClosed)
except Exception:  # noqa: BLE001 — optional dependency surface
    pass
try:  # uvicorn wraps it as ClientDisconnected when an ASGI send races the close
    from uvicorn.protocols.utils import ClientDisconnected as _ClientDisconnected

    _DISCONNECT_EXCS = (*_DISCONNECT_EXCS, _ClientDisconnected)
except Exception:  # noqa: BLE001
    pass

from relay.auth.jwt import AuthError, Claims, verify_token
from relay.auth.rls import set_current_claims
from relay.config import settings
from relay.ids import new_id
from relay.logging import get_logger
from relay.schemas.common import build_event

logger = get_logger("relay.gateway.ws")

# WebSocket close codes (RFC 6455 + application range).
WS_POLICY_VIOLATION = 1008
WS_INTERNAL_ERROR = 1011


def _now_iso() -> str:
    """Current ISO-8601 UTC timestamp (the one place WS code reads the clock)."""
    return datetime.now(tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Hub
# ---------------------------------------------------------------------------


class WsHub:
    """In-process registry of live WebSocket connections, keyed by ``session_id``.

    A single session may have multiple sockets (e.g. an operator dashboard plus the
    agent debug view), so each session maps to a *set* of sockets. All mutation is
    guarded by an :class:`asyncio.Lock` to keep the registry consistent under concurrent
    connects/disconnects.

    :meth:`broadcast` fans an already-built envelope dict out to every socket on a
    session; dead sockets are pruned on send failure so a crashed client cannot wedge
    the fan-out.
    """

    # Redis channel all processes publish/subscribe on for cross-process fan-out.
    _REDIS_CHANNEL = "relay:ws"

    def __init__(self) -> None:
        self._sessions: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()
        # Cross-process pub/sub (optional). Enabled by start_redis(); when disabled the
        # hub is purely in-process (tests, single-process dev).
        self._origin = uuid.uuid4().hex  # identifies THIS process's own publishes
        self._redis: Any | None = None
        self._pubsub: Any | None = None
        self._sub_task: asyncio.Task | None = None
        # The event loop the current redis client/pubsub were created on. LiveKit Agents
        # runs each job in a FRESH event loop, and aioredis futures are bound to the loop
        # that created them — so we must rebuild the client whenever the running loop
        # differs from this one, or every publish raises "Future attached to a different
        # loop" and agent→dashboard delivery silently breaks.
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # Cross-process pub/sub (Redis)
    # ------------------------------------------------------------------
    async def start_redis(self) -> None:
        """Connect the Redis publisher + subscriber so broadcasts cross processes.

        Idempotent *within the same event loop*. Called from the gateway lifespan and
        the agent worker entrypoint. If the running loop differs from the one the current
        client was built on (LiveKit Agents spawns a new loop per job), the old client is
        dropped and a fresh one bound to THIS loop is created — otherwise every publish
        would raise "Future attached to a different loop".
        """
        try:
            current_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._redis is not None:
            same_loop = self._loop is not None and self._loop is current_loop
            healthy = same_loop and self._sub_task is not None and not self._sub_task.done()
            if healthy:
                return  # live connection on this very loop — nothing to do
            if same_loop:
                # Same loop but the subscriber died — graceful close is safe.
                await self.stop_redis()
            else:
                # Different (or previous job's) loop: do NOT await the old objects'
                # teardown — that would itself raise cross-loop. Just drop the refs.
                logger.info("ws hub: event loop changed, rebuilding redis client")
                self._reset_redis_refs()

        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(settings.redis_url)
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(self._REDIS_CHANNEL)
        self._sub_task = asyncio.create_task(self._subscribe_loop())
        self._loop = current_loop
        logger.info("ws hub redis enabled", extra={"channel": self._REDIS_CHANNEL})

    def _reset_redis_refs(self) -> None:
        """Drop redis references WITHOUT awaiting (for a dead/foreign event loop).

        Cancelling a task that belongs to a no-longer-running loop is a harmless marker;
        we deliberately do not await aclose() on the old client because those coroutines
        are bound to the old loop and would raise here.
        """
        if self._sub_task is not None:
            try:
                self._sub_task.cancel()
            except Exception:  # noqa: BLE001
                pass
        self._sub_task = None
        self._pubsub = None
        self._redis = None
        self._loop = None

    async def stop_redis(self) -> None:
        """Tear down the Redis pub/sub (gateway shutdown)."""
        if self._sub_task is not None:
            self._sub_task.cancel()
            self._sub_task = None
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(self._REDIS_CHANNEL)
                await self._pubsub.aclose()
            except Exception:  # pragma: no cover - best effort
                pass
            self._pubsub = None
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:  # pragma: no cover
                pass
            self._redis = None
        self._loop = None

    async def _subscribe_loop(self) -> None:
        """Deliver Redis-published events (from other processes) to local sockets."""
        assert self._pubsub is not None
        try:
            async for message in self._pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    payload = json.loads(message["data"])
                except Exception:
                    continue
                if payload.get("origin") == self._origin:
                    continue  # our own publish; already delivered locally
                await self._local_broadcast(payload["session_id"], payload["event"])
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            raise
        except Exception as exc:  # pragma: no cover - resilience
            logger.warning("ws redis subscribe loop ended", extra={"error": str(exc)})

    async def register(self, session_id: str, ws: WebSocket) -> None:
        """Add ``ws`` to ``session_id``'s connection set. The socket must already be
        accepted by the caller (the route owns ``accept()``/close semantics)."""
        async with self._lock:
            self._sessions.setdefault(session_id, set()).add(ws)
        logger.info(
            "ws registered",
            extra={"session_id": session_id, "connections": self.connection_count(session_id)},
        )

    async def unregister(self, session_id: str, ws: WebSocket) -> None:
        """Remove ``ws`` from ``session_id``; drop the session entry when empty."""
        async with self._lock:
            conns = self._sessions.get(session_id)
            if conns is not None:
                conns.discard(ws)
                if not conns:
                    self._sessions.pop(session_id, None)
        logger.info(
            "ws unregistered",
            extra={"session_id": session_id, "connections": self.connection_count(session_id)},
        )

    async def broadcast(self, session_id: str, event: dict[str, Any]) -> None:
        """Fan an envelope to ``session_id``'s sockets — locally and across processes.

        Delivers to sockets owned by THIS process immediately, then (if Redis is enabled)
        publishes so other processes (e.g. the agent worker → the gateway holding the
        browser socket) deliver to theirs. With Redis disabled this is purely local.
        """
        await self._local_broadcast(session_id, event)
        if self._redis is not None:
            try:
                await self._redis.publish(
                    self._REDIS_CHANNEL,
                    json.dumps(
                        {"origin": self._origin, "session_id": session_id, "event": event}
                    ),
                )
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("ws redis publish failed", extra={"error": str(exc)})

    async def _local_broadcast(self, session_id: str, event: dict[str, Any]) -> None:
        """Send a pre-built ``{type, ts, data}`` envelope to every LOCAL socket on ``session_id``.

        Sockets that error on send (closed/broken) are collected and unregistered so the
        registry self-heals. No-op when the session has no live connections in this process.
        """
        async with self._lock:
            targets = list(self._sessions.get(session_id, ()))
        if not targets:
            return

        dead: list[WebSocket] = []
        for ws in targets:
            try:
                if ws.application_state != WebSocketState.CONNECTED:
                    dead.append(ws)
                    continue
                await ws.send_json(event)
            except Exception:  # broken pipe / already closed
                dead.append(ws)
        for ws in dead:
            await self.unregister(session_id, ws)

    def connection_count(self, session_id: str) -> int:
        """Number of live sockets currently registered for ``session_id``."""
        return len(self._sessions.get(session_id, ()))

    def has_session(self, session_id: str) -> bool:
        """Whether any socket is currently registered for ``session_id``."""
        return bool(self._sessions.get(session_id))


# Process-wide singleton. The agent worker and REST paths import THIS instance so events
# reach the same set of sockets that the gateway accepted.
hub = WsHub()

# A SECOND hub for the public customer-facing widget sockets, keyed by ``thread_id``
# (not session_id). Reuses the same register/broadcast/Redis machinery so widget events
# fan out cross-process too. Kept separate from ``hub`` so the rep (session_id) and
# customer (thread_id) channels never collide on a shared key space.
inbound_hub = WsHub()


def get_hub() -> WsHub:
    """Return the process-singleton :class:`WsHub`."""
    return hub


# ---------------------------------------------------------------------------
# Inbound channel: thread <-> session mapping + the customer-message pipeline
# ---------------------------------------------------------------------------

# Deterministic prefix used to derive a session id from a thread id (and to stamp the
# thread id onto the Session.livekit_room field for recovery). See INBOUND_CONTRACT.
_INBOUND_PREFIX = "inbound:"

# In-process bidirectional map so /sessions/{id}/reply can recover the thread id and the
# inbound routes can recover the session id without a DB round-trip. Best-effort cache;
# the authoritative mapping is always re-derivable via stable_session_id().
_thread_to_session: dict[str, str] = {}
_session_to_thread: dict[str, str] = {}


def inbound_session_id(thread_id: str) -> str:
    """Deterministic ``session_id`` for an inbound ``thread_id`` (contract mapping)."""
    from relay.ids import stable_session_id

    return stable_session_id(_INBOUND_PREFIX + thread_id)


def register_thread(thread_id: str) -> str:
    """Cache + return the ``session_id`` for ``thread_id`` (idempotent)."""
    session_id = inbound_session_id(thread_id)
    _thread_to_session[thread_id] = session_id
    _session_to_thread[session_id] = thread_id
    return session_id


def thread_for_session(session_id: str) -> str | None:
    """Recover the inbound ``thread_id`` for a ``session_id`` if it is an inbound thread."""
    return _session_to_thread.get(session_id)


def _inbound_org_id() -> str:
    """The org that owns the inbound demo tenant (falls back to the default org)."""
    return settings.inbound_org_id or settings.default_org_id


async def deliver_to_widget(thread_id: str, role: str, text: str) -> None:
    """Push a ``message`` envelope to the customer widget socket(s) for ``thread_id``.

    ``role`` is ``"customer"`` or ``"agent"``. Best-effort fan-out via the inbound hub
    (local + Redis); never raises into the caller.
    """
    await inbound_hub.broadcast(
        thread_id,
        build_event("message", {"role": role, "text": text}, _now_iso()),
    )


async def _widget_status(thread_id: str, *, routed_to: str | None, agent_typing: bool) -> None:
    """Push a ``status`` envelope to the widget (routing badge + typing indicator)."""
    data: dict[str, Any] = {"agent_typing": agent_typing}
    if routed_to is not None:
        data["routed_to"] = routed_to
    await inbound_hub.broadcast(thread_id, build_event("status", data, _now_iso()))


async def _ensure_inbound_session(session_id: str, thread_id: str, org_id: str) -> None:
    """Idempotently ensure the inbound Session row exists (mirrors the demo-room pattern).

    Stamps ``livekit_room = "inbound:" + thread_id`` so the thread id is recoverable from
    the row alone (the in-process map is just a fast cache). Best-effort: a failure here
    must never 500 the widget — the pipeline degrades to broadcast-only.
    """
    from relay.db.base import privileged_session
    from relay.db.models import Session

    try:
        async with privileged_session() as db:
            existing = await db.get(Session, session_id)
            if existing is None:
                db.add(
                    Session(
                        id=session_id,
                        organization_id=org_id,
                        mode="desk",
                        livekit_room=_INBOUND_PREFIX + thread_id,
                        status="active",
                        started_at=datetime.now(tz=timezone.utc),
                    )
                )
    except Exception as exc:  # noqa: BLE001 — best-effort; widget must still work
        logger.warning("inbound session ensure failed", extra={"error": str(exc), "thread_id": thread_id})


async def _persist_inbound_utterance(
    *, session_id: str, org_id: str, speaker: str, text: str
) -> str:
    """Persist an inbound :class:`Utterance` (speaker ``customer``/``agent``); return its id.

    Best-effort: on failure we still return a fresh utterance id so the WS broadcast can
    proceed — transcript continuity matters more than durable storage for the demo.
    """
    import uuid as _uuid

    from relay.db.base import privileged_session
    from relay.db.models import Utterance

    utterance_id = new_id("utt")
    try:
        async with privileged_session() as db:
            db.add(
                Utterance(
                    id=utterance_id,
                    session_id=session_id,
                    organization_id=_uuid.UUID(org_id),
                    speaker=speaker,
                    text=text,
                    ts=datetime.now(tz=timezone.utc),
                )
            )
    except Exception as exc:  # noqa: BLE001 — never break the channel on a write error
        logger.warning(
            "inbound utterance persist failed",
            extra={"error": str(exc), "session_id": session_id, "speaker": speaker},
        )
    return utterance_id


# Department display labels for the routing badge (rep + widget).
_DEPT_LABELS: dict[str, str] = {
    "support": "Customer Support",
    "sales": "Sales",
    "it": "IT",
}
# Departments whose questions Desk answers from the docs (grounded resolution). Sales is
# handled by Intake (lead focus), so it does NOT get a Desk card.
_DESK_DEPARTMENTS = {"support", "it"}


async def _inbound_classify(text: str) -> tuple[str, str, float]:
    """Classify a customer message → ``(intent, label, confidence)``.

    intent is ``"support"`` | ``"sales"`` | ``"it"``; label is the human department name.
    Uses the shared LLM client's ``classify_intent`` (heuristic default if no adapter).
    Defaults to ``support`` (answer the question) when adapters are unavailable.
    """
    intent = "support"
    confidence = 0.5
    try:
        _ensure_adapters()
        intent = await _llm_client.classify_intent(text=text)
        confidence = 0.8
    except Exception as exc:  # noqa: BLE001 — degrade to support
        logger.info("intent classify unavailable; defaulting to support", extra={"error": str(exc)})
    if intent not in _DEPT_LABELS:
        intent = "support"
    return intent, _DEPT_LABELS[intent], confidence


async def handle_inbound_message(
    *,
    thread_id: str,
    text: str,
    display_name: str | None = None,
) -> None:
    """The full server pipeline for one inbound customer message (INBOUND_CONTRACT §pipeline).

    Every step is best-effort and isolated — the widget is never 500'd. Steps:
      1. Resolve session_id + org_id; ensure the Session row.
      2. Persist ``Utterance(speaker="customer")``.
      3. Echo to the widget (``message`` role=customer).
      4. Notify the rep session (``transcript.final``).
      5. Classify intent → broadcast routing to BOTH sockets.
      6. ALWAYS run Intake triage in parallel (``lead.update`` to rep).
      7. If support → run the Desk grounded synthesis (``mode="desk"``) → card.* to rep.
      8. Clear the widget typing indicator.
    """
    text = (text or "").strip()
    if not text:
        return

    session_id = register_thread(thread_id)
    org_id = _inbound_org_id()

    await _ensure_inbound_session(session_id, thread_id, org_id)

    # (2) persist + (3) echo to widget + (4) notify rep.
    utterance_id = await _persist_inbound_utterance(
        session_id=session_id, org_id=org_id, speaker="customer", text=text
    )
    await deliver_to_widget(thread_id, "customer", text)
    await hub.broadcast(
        session_id,
        build_event(
            "transcript.final",
            {"utterance_id": utterance_id, "speaker": "customer", "text": text},
            _now_iso(),
        ),
    )

    # (5) classify intent → routing to both sockets. department is the intent key
    # (support|sales|it); label is the human department name shown on the badges.
    department, label, confidence = await _inbound_classify(text)
    await hub.broadcast(
        session_id,
        build_event(
            "session.status",
            {
                "status": "active",
                "retrieval_backend": _current_retrieval_backend(),
                "routing": {"department": department, "label": label, "confidence": confidence},
            },
            _now_iso(),
        ),
    )
    await _widget_status(thread_id, routed_to=label, agent_typing=True)

    # (6) Intake triage ALWAYS runs in parallel — accumulate transcript, re-extract lead.
    #     Returns the lead payload so the Sales path can ask-for-info / forward.
    intake_task = asyncio.create_task(
        _inbound_intake(session_id=session_id, org_id=org_id, text=text)
    )

    # (7) Support / IT → Desk grounded synthesis from the docs (cards to the rep).
    if department in _DESK_DEPARTMENTS:
        try:
            await _inbound_desk_synthesis(session_id=session_id, org_id=org_id, text=text)
        except Exception as exc:  # noqa: BLE001 — never break the channel
            logger.warning("inbound desk synthesis failed", extra={"error": str(exc), "thread_id": thread_id})

    # Let the parallel intake finish (we need the lead for the Sales follow-up).
    lead: dict[str, Any] | None = None
    try:
        lead = await intake_task
    except Exception as exc:  # noqa: BLE001
        logger.warning("inbound intake failed", extra={"error": str(exc), "thread_id": thread_id})

    # (7b) Sales → ask the customer for the missing qualifying info, or forward to Sales
    #      once the lead is complete enough. (Intake already qualified it above.)
    if department == "sales":
        try:
            await _inbound_sales_followup(
                session_id=session_id, thread_id=thread_id, org_id=org_id, lead=lead
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("inbound sales follow-up failed", extra={"error": str(exc), "thread_id": thread_id})

    # (8) Done — clear the widget typing indicator (keep the routing badge).
    await _widget_status(thread_id, routed_to=label, agent_typing=False)


# Per-thread accumulated customer transcript (Intake re-extracts over the whole convo so a
# late-arriving name updates the lead live). Bounded to the recent tail at send time.
_inbound_transcripts: dict[str, list[str]] = {}


async def _inbound_intake(
    *, session_id: str, org_id: str, text: str
) -> dict[str, Any] | None:
    """Accumulate the customer turn and re-run Intake extraction → ``lead.update`` to rep.

    Returns the lead payload (so the caller can drive the Sales ask-for-info / forward
    follow-up), or ``None`` if nothing could be extracted yet.
    """
    transcript = _inbound_transcripts.setdefault(session_id, [])
    transcript.append(text)

    try:
        _ensure_adapters()
    except _OrchestratorUnavailable as exc:
        logger.info("inbound intake adapters unavailable", extra={"error": str(exc)})
        return None

    async def _emit(event_type: str, data: dict[str, Any]) -> None:
        await hub.broadcast(session_id, build_event(event_type, data, _now_iso()))

    from relay.orchestrator.intake import extract_and_store

    return await extract_and_store(
        session_id=session_id,
        org_id=org_id,
        transcript=" ".join(transcript[-40:]),
        llm=_llm_client,
        emit=_emit,
    )


# Friendly prompts for the BANT fields the Sales agent still needs from the customer.
_BANT_PROMPTS: dict[str, str] = {
    "budget": "the budget you're working with",
    "authority": "who'll be involved in the decision",
    "need": "what you're hoping to solve",
    "timeline": "your timeline",
}
# How complete a lead must be before we auto-forward it to Sales.
_FORWARD_SCORE = 70


async def _inbound_agent_message(
    *, session_id: str, thread_id: str, org_id: str, text: str
) -> None:
    """Send an AGENT message to the customer widget AND the rep conversation + persist it.

    Used by the Sales follow-up (ask-for-info / forward confirmation) so the customer gets
    a real reply and the rep sees it in the Desk conversation.
    """
    await deliver_to_widget(thread_id, "agent", text)
    utt_id = await _persist_inbound_utterance(
        session_id=session_id, org_id=org_id, speaker="agent", text=text
    )
    await hub.broadcast(
        session_id,
        build_event(
            "transcript.final",
            {"utterance_id": utt_id, "speaker": "agent", "text": text},
            _now_iso(),
        ),
    )


async def _inbound_sales_followup(
    *, session_id: str, thread_id: str, org_id: str, lead: dict[str, Any] | None
) -> None:
    """Sales path: ask the customer for missing BANT, or forward to Sales when complete.

    - If the lead is already routed → do nothing (we forwarded earlier).
    - If complete enough (all BANT present OR score ≥ threshold) → route to Sales (Slack),
      mark the lead routed, and send the customer a confirmation.
    - Otherwise → ask the customer for the 1–2 most important missing fields so Intake can
      finish qualifying. Desk does the asking; the customer's answer re-runs this flow.
    """
    if not lead:
        # Nothing qualified yet — greet + ask broadly so the conversation can start.
        await _inbound_agent_message(
            session_id=session_id, thread_id=thread_id, org_id=org_id,
            text=(
                "Happy to help get you to the right team! To point you to the best person, "
                "could you tell me a bit about what you're looking for and your timeline?"
            ),
        )
        return

    if lead.get("routed_to"):
        return  # already forwarded

    quals = lead.get("qualifiers") or {}
    missing = [f for f in ("budget", "authority", "need", "timeline") if not quals.get(f)]
    complete = not missing or int(lead.get("score") or 0) >= _FORWARD_SCORE

    if complete:
        # Forward to Sales: route the lead (Slack, best-effort) + confirm to the customer.
        lead_id = lead.get("lead_id")
        try:
            from relay.adapters.slack import SlackNotifier

            notifier = SlackNotifier()
            await notifier.route_lead(
                f"*New qualified lead → #sales*\n"
                f"*Name:* {lead.get('name')}  *Company:* {lead.get('company')}\n"
                f"*Email:* {lead.get('email')}  *Score:* {lead.get('score')}/100\n"
                f"*Qualifiers:* {quals}"
            )
        except Exception as exc:  # noqa: BLE001 — Slack optional
            logger.info("sales forward slack skipped", extra={"error": str(exc)})
        # Persist routed_to so we don't forward twice + the rep sees it.
        await _mark_lead_routed(session_id=session_id, org_id=org_id, channel="#sales")
        await _inbound_agent_message(
            session_id=session_id, thread_id=thread_id, org_id=org_id,
            text=(
                "Perfect — thank you! I've forwarded your details to our Sales team and "
                "they'll reach out to you shortly. Is there anything else I can help with?"
            ),
        )
        logger.info("inbound sales lead forwarded", extra={"session_id": session_id, "lead_id": lead_id})
        return

    # Still missing info → ask for the 1–2 most important missing fields.
    ask_for = [_BANT_PROMPTS[f] for f in missing][:2]
    if len(ask_for) == 2:
        joined = f"{ask_for[0]} and {ask_for[1]}"
    else:
        joined = ask_for[0]
    await _inbound_agent_message(
        session_id=session_id, thread_id=thread_id, org_id=org_id,
        text=(
            f"Thanks for your interest! To connect you with the right person on our Sales "
            f"team, could you share {joined}?"
        ),
    )


async def _mark_lead_routed(*, session_id: str, org_id: str, channel: str) -> None:
    """Set ``routed_to`` on the session's lead so it isn't forwarded twice; emit lead.update."""
    from sqlalchemy import select

    from relay.db.base import privileged_session
    from relay.db.models import Lead as LeadModel
    from relay.schemas.leads import lead_to_schema

    try:
        async with privileged_session() as db:
            lead = (
                await db.execute(select(LeadModel).where(LeadModel.session_id == session_id))
            ).scalar_one_or_none()
            if lead is None or lead.routed_to:
                return
            lead.routed_to = channel
            await db.flush()
            payload = lead_to_schema(lead).model_dump()
        await hub.broadcast(session_id, build_event("lead.update", payload, _now_iso()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("mark lead routed failed", extra={"error": str(exc), "session_id": session_id})


async def _inbound_desk_synthesis(*, session_id: str, org_id: str, text: str) -> None:
    """Run the Desk grounded synthesis for an inbound support message (cards to the rep).

    Streams ``card.new``/``card.update`` to the rep session via ``hub``. Grounded-or-silent:
    a ``None`` result emits nothing. Uses a privileged DB session (no request scope here)
    and the customer-ready ``mode="desk"`` prompt.
    """

    async def _emit(event_type: str, data: dict[str, Any]) -> None:
        await hub.broadcast(session_id, build_event(event_type, data, _now_iso()))

    from relay.db.base import privileged_session

    try:
        async with privileged_session() as db:
            orchestrator = _build_orchestrator(db)
            await orchestrator.synthesize(
                session_id=session_id,
                org_id=org_id,
                mode="desk",
                query_text=text,
                customer_id=None,
                emit=_emit,
            )
    except _OrchestratorUnavailable as exc:
        logger.warning("inbound orchestrator unavailable", extra={"error": str(exc)})


# ---------------------------------------------------------------------------
# Inbound client-event handling
# ---------------------------------------------------------------------------


async def _handle_query_manual(
    *,
    session_id: str,
    claims: Claims,
    mode: str,
    text: str,
    customer_id: str | None,
) -> None:
    """Run the grounding-guarded Orchestrator for a manual query and broadcast the card.

    Grounded-or-silent: a ``None`` result from the Orchestrator (no relevant chunk, or the
    LLM declined) produces NO event — never a fabricated card, never an error. Orchestrator
    wiring (retrieval/LLM adapters) is constructed elsewhere; we import lazily so this
    module stays importable while those packages are still being built in parallel.
    """
    if not text or not text.strip():
        return

    # Stream tokens straight to this session's sockets as they arrive: the first
    # token paints immediately instead of waiting for the full completion.
    async def _emit(event_type: str, data: dict[str, Any]) -> None:
        await hub.broadcast(session_id, build_event(event_type, data, _now_iso()))

    # The Orchestrator persists the Card/CardSource rows, so it needs a DB session.
    # We open a privileged session here (the WS task has no request-scoped session) and
    # the Orchestrator's denormalised ``organization_id`` writes keep tenant scoping.
    from relay.db.base import privileged_session

    try:
        async with privileged_session() as db:
            orchestrator = _build_orchestrator(db)
            card = await orchestrator.synthesize(
                session_id=session_id,
                org_id=claims.org_id,
                mode=mode,
                query_text=text,
                customer_id=customer_id,
                emit=_emit,
            )
    except _OrchestratorUnavailable as exc:
        logger.warning("orchestrator unavailable for query.manual", extra={"error": str(exc)})
        await hub.broadcast(
            session_id,
            build_event(
                "error",
                {"code": "retrieval_unavailable", "message": "query engine unavailable"},
                _now_iso(),
            ),
        )
        return
    except Exception as exc:
        logger.error("orchestrator.synthesize failed", extra={"error": str(exc), "session_id": session_id})
        await hub.broadcast(
            session_id,
            build_event(
                "error",
                {"code": "internal_error", "message": "failed to synthesize answer"},
                _now_iso(),
            ),
        )
        return

    # The card (and its sources) was already streamed to the client via _emit
    # (card.new + card.update). A None result is the grounded-or-silent contract:
    # no card was produced, and nothing was emitted. Either way, nothing to do here.
    return


async def _handle_intake_turn(
    *,
    session_id: str,
    claims: Claims,
    text: str,
    state: dict[str, Any],
) -> None:
    """Accumulate an Intake transcript turn, re-extract the lead, broadcast lead.update.

    Best-effort and silent on failure: if extraction yields nothing yet (too little
    said), no lead card appears — consistent with the grounded-or-silent ethos.
    """
    if not text or not text.strip():
        return

    transcript: list[str] = state.setdefault("intake_transcript", [])
    transcript.append(text.strip())
    # Echo the turn into the transcript pane so the operator sees what was captured.
    await hub.broadcast(
        session_id,
        build_event(
            "transcript.final",
            {"utterance_id": new_id("utt"), "speaker": "prospect", "text": text.strip()},
            _now_iso(),
        ),
    )

    async def _emit(event_type: str, data: dict[str, Any]) -> None:
        await hub.broadcast(session_id, build_event(event_type, data, _now_iso()))

    try:
        _ensure_adapters()
    except _OrchestratorUnavailable as exc:
        logger.warning("intake adapters unavailable", extra={"error": str(exc)})
        return

    from relay.orchestrator.intake import extract_and_store

    try:
        await extract_and_store(
            session_id=session_id,
            org_id=claims.org_id,
            transcript=" ".join(transcript[-40:]),
            llm=_llm_client,
            emit=_emit,
        )
    except Exception as exc:  # noqa: BLE001 — never break the socket on extraction error
        logger.warning("intake extraction failed", extra={"error": str(exc), "session_id": session_id})


class _OrchestratorUnavailable(RuntimeError):
    """Raised when the Orchestrator/adapters cannot be constructed (missing creds)."""


# Process-wide, session-independent singletons. The LLM client owns a pooled httpx
# connection (keep-alive) — reusing it across queries keeps the TFY gateway connection
# warm so the latency-critical card path never pays a fresh TLS handshake. Only the
# per-call Orchestrator (which binds a DB session) is constructed each query.
_llm_client: Any | None = None
_retrieval_service: Any | None = None
_memory_service: Any | None = None
_adapters_ready = False


def _ensure_adapters() -> None:
    """Build (once) the shared LLM + retrieval + memory adapters, or raise."""
    global _llm_client, _retrieval_service, _memory_service, _adapters_ready
    if _adapters_ready:
        return
    try:
        from relay.adapters.llm_tfy import TfyLLMClient
        from relay.retrieval.service import CompositeRetrievalService

        _retrieval_service = CompositeRetrievalService.from_settings()
        _llm_client = TfyLLMClient()

        # Desk-mode per-customer memory (Moss-backed, built-in embeddings). Best-effort:
        # if it can't be built, proceed without memory (optional context, never grounding).
        try:
            from relay.memory.moss_memory import MossMemoryService

            _memory_service = MossMemoryService()
        except Exception as exc:  # noqa: BLE001
            logger.info("memory service unavailable; Desk runs without it", extra={"error": str(exc)})
            _memory_service = None

        _adapters_ready = True
    except Exception as exc:  # noqa: BLE001 — missing creds / adapters under construction
        raise _OrchestratorUnavailable(str(exc)) from exc


def _build_orchestrator(db):
    """Construct the Orchestrator with the composite retrieval + LLM adapters.

    The LLM/retrieval/memory adapters are process-wide singletons (built once, reused
    so the gateway connection stays warm); only the Orchestrator — which binds the
    supplied DB session for Card/CardSource writes — is built per call. Tests
    monkeypatch this function to inject a deterministic in-memory Orchestrator.
    Raises :class:`_OrchestratorUnavailable` if a piece is missing.
    """
    _ensure_adapters()
    from relay.orchestrator.synth import Orchestrator

    return Orchestrator(
        retrieval=_retrieval_service,
        llm=_llm_client,
        session=db,
        memory=_memory_service,
    )


def get_llm_client() -> Any:
    """Return the shared LLM client singleton (building adapters if needed)."""
    _ensure_adapters()
    return _llm_client


async def prewarm_llm() -> None:
    """Best-effort: build the shared adapters and open the gateway keep-alive
    connection so the first live query doesn't pay the TLS/handshake cost.
    Safe to call on session/agent start; never raises.
    """
    try:
        _ensure_adapters()
    except _OrchestratorUnavailable as exc:
        logger.info("prewarm skipped (adapters unavailable)", extra={"error": str(exc)})
        return
    prewarm = getattr(_llm_client, "prewarm", None)
    if prewarm is not None:
        try:
            await prewarm()
        except Exception as exc:  # noqa: BLE001
            logger.debug("llm prewarm failed", extra={"error": str(exc)})


async def _process_client_event(
    *,
    raw: dict[str, Any],
    session_id: str,
    claims: Claims,
    state: dict[str, Any],
) -> None:
    """Validate and dispatch a single inbound client event.

    ``state`` is per-connection mutable state (currently just the active ``mode``, which
    ``mode.set`` updates and ``query.manual`` reads). Unknown event types are ignored.
    """
    etype = raw.get("type")
    data = raw.get("data") or {}
    if not isinstance(data, dict):
        data = {}

    if etype == "mode.set":
        mode = data.get("mode")
        if isinstance(mode, str) and mode:
            state["mode"] = mode
        return

    if etype == "query.manual":
        text = data.get("text")
        if isinstance(text, str):
            mode = state.get("mode", "live")
            if mode == "intake":
                # In Intake, a typed message is a transcript turn — accumulate it and
                # re-extract/score the lead rather than synthesising a grounded card.
                await _handle_intake_turn(
                    session_id=session_id, claims=claims, text=text, state=state
                )
            else:
                await _handle_query_manual(
                    session_id=session_id,
                    claims=claims,
                    mode=mode,
                    text=text,
                    customer_id=data.get("customer_id"),
                )
        return

    if etype in ("card.pin", "card.dismiss"):
        # Pin/dismiss are client-side UX signals; no server state is mutated here yet.
        # Acknowledged by relaying nothing (the client owns its own card list ordering).
        return

    # Unknown / unsupported client event -> ignore (forward-compatible).
    logger.info("ignoring unknown client event", extra={"session_id": session_id, "event_type": etype})


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.websocket("/ws/sessions/{session_id}")
async def session_ws(
    websocket: WebSocket,
    session_id: str,
    token: str | None = Query(default=None),
) -> None:
    """Bidirectional session event stream.

    Handshake: verify ``?token=`` (Supabase JWT), enforce the configured origin, accept the
    socket, register with the hub, and emit an initial ``session.status``. Then loop reading
    client events until disconnect.
    """
    # --- Origin check (before accept) -------------------------------------------------
    # FRONTEND_ORIGIN may be a comma-separated list; compare against the parsed allow-list.
    origin = websocket.headers.get("origin")
    allowed = settings.cors_origins
    if origin and allowed and origin.rstrip("/") not in allowed:
        logger.warning("ws origin rejected", extra={"origin": origin})
        await websocket.close(code=WS_POLICY_VIOLATION)
        return

    # --- Token verification (before accept) -------------------------------------------
    token = token or websocket.query_params.get("token")
    if not token:
        await websocket.close(code=WS_POLICY_VIOLATION)
        return
    try:
        claims = await verify_token(token)
        # Enrich the claims with a concrete org_id (and role) the same way the REST
        # auth dependency does. A raw Supabase JWT does NOT carry org_id — it is
        # resolved from the user's org membership in the DB. Without this, every WS
        # manual query ran with org_id=None, so Desk/Intake retrieval and the lead
        # insert failed (Moss "Filter value must be…", leads NOT NULL violation).
        from relay.auth.deps import _bootstrap_principal

        claims = await _bootstrap_principal(claims)
    except AuthError as exc:
        logger.warning("ws token rejected", extra={"error": str(exc)})
        await websocket.close(code=WS_POLICY_VIOLATION)
        return

    # Publish claims so any DB access inside this connection is RLS-scoped.
    set_current_claims(claims)

    await websocket.accept()
    await hub.register(session_id, websocket)

    # Warm the LLM gateway connection in the background so the first manual query
    # doesn't pay the TLS handshake. Never blocks the handshake; never raises.
    asyncio.create_task(prewarm_llm())

    state: dict[str, Any] = {"mode": "live"}
    try:
        # Initial session.status — INSIDE the try so a client that drops right after
        # connecting (a refresh / rapid reconnect) is handled as a clean disconnect
        # rather than an unhandled ASGI exception traceback.
        backend = _current_retrieval_backend()
        await websocket.send_json(
            build_event(
                "session.status",
                {"status": "active", "retrieval_backend": backend},
                _now_iso(),
            )
        )

        while True:
            raw = await websocket.receive_json()
            if not isinstance(raw, dict):
                continue
            # Re-publish claims on each turn: the contextvar is per-task and a long-lived
            # socket may otherwise lose it across awaits in some runtimes.
            set_current_claims(claims)
            await _process_client_event(
                raw=raw, session_id=session_id, claims=claims, state=state
            )
    except _DISCONNECT_EXCS:
        pass  # client went away — normal, not an error
    except Exception as exc:  # malformed frame, transport error
        logger.warning("ws loop error", extra={"error": str(exc), "session_id": session_id})
        try:
            await websocket.close(code=WS_INTERNAL_ERROR)
        except Exception:
            pass
    finally:
        await hub.unregister(session_id, websocket)


@router.websocket("/ws/inbound/{thread_id}")
async def inbound_ws(websocket: WebSocket, thread_id: str) -> None:
    """Public customer-widget event stream (NO auth — INBOUND_CONTRACT).

    Accepts the socket, registers it on the inbound hub keyed by ``thread_id``, then loops:
    server→client it receives ``message``/``status`` envelopes (fanned out by the pipeline);
    client→server it MAY send ``{type:"message", data:{text}}``, treated identically to the
    REST POST. The widget is unauthenticated by design (demo scope; gate behind a token
    before prod). No origin lock — the customer site is a separate public origin.
    """
    await websocket.accept()
    register_thread(thread_id)
    await inbound_hub.register(thread_id, websocket)

    # Ensure cross-process fan-out: the pipeline may run in another process (or this one).
    try:
        await inbound_hub.start_redis()
    except Exception as exc:  # noqa: BLE001 — degrade to local-only
        logger.warning("inbound hub redis disabled", extra={"error": str(exc)})

    # Warm the LLM gateway connection so the first classify/synthesis doesn't pay TLS.
    asyncio.create_task(prewarm_llm())

    try:
        while True:
            raw = await websocket.receive_json()
            if not isinstance(raw, dict):
                continue
            if raw.get("type") != "message":
                continue
            data = raw.get("data") or {}
            text = data.get("text") if isinstance(data, dict) else None
            if isinstance(text, str) and text.strip():
                display_name = data.get("display_name") if isinstance(data, dict) else None
                # Run the same pipeline as the REST POST. Fire-and-forget so the socket
                # keeps reading while synthesis streams back out through the hub.
                asyncio.create_task(
                    handle_inbound_message(
                        thread_id=thread_id, text=text, display_name=display_name
                    )
                )
    except _DISCONNECT_EXCS:
        pass  # client went away — normal, not an error
    except Exception as exc:  # malformed frame, transport error
        logger.warning("inbound ws loop error", extra={"error": str(exc), "thread_id": thread_id})
        try:
            await websocket.close(code=WS_INTERNAL_ERROR)
        except Exception:
            pass
    finally:
        await inbound_hub.unregister(thread_id, websocket)


def _current_retrieval_backend() -> str:
    """Best-effort primary retrieval backend label for ``session.status``.

    Defaults to ``"moss"`` (the primary path). Falls back to ``"pgvector"`` if Moss is not
    configured. Never raises — this is a cosmetic status hint.
    """
    try:
        if settings.moss_api_key and settings.moss_base_url:
            return "moss"
    except Exception:
        pass
    return "pgvector"


__all__ = [
    "WsHub",
    "hub",
    "inbound_hub",
    "get_hub",
    "router",
    "session_ws",
    "inbound_ws",
    "prewarm_llm",
    "deliver_to_widget",
    "handle_inbound_message",
    "inbound_session_id",
    "register_thread",
    "thread_for_session",
]

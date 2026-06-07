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

from relay.auth.jwt import AuthError, Claims, verify_token
from relay.auth.rls import set_current_claims
from relay.config import settings
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

    # ------------------------------------------------------------------
    # Cross-process pub/sub (Redis)
    # ------------------------------------------------------------------
    async def start_redis(self) -> None:
        """Connect the Redis publisher + subscriber so broadcasts cross processes.

        Idempotent. Called from the gateway lifespan and the agent worker entrypoint.
        The agent process publishes (it holds no browser sockets); the gateway process
        publishes AND subscribes (its subscriber delivers agent-published events to the
        browser sockets it owns). Messages tagged with this process's own origin are
        skipped on receipt to avoid double-delivery.
        """
        if self._redis is not None:
            return
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(settings.redis_url)
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(self._REDIS_CHANNEL)
        self._sub_task = asyncio.create_task(self._subscribe_loop())
        logger.info("ws hub redis enabled", extra={"channel": self._REDIS_CHANNEL})

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


def get_hub() -> WsHub:
    """Return the process-singleton :class:`WsHub`."""
    return hub


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

    if card is None:
        # No grounding -> silent. The contract surfaces this as the absence of a card.
        return

    payload = card.model_dump() if hasattr(card, "model_dump") else dict(card)
    await hub.broadcast(session_id, build_event("card.new", payload, _now_iso()))


class _OrchestratorUnavailable(RuntimeError):
    """Raised when the Orchestrator/adapters cannot be constructed (missing creds)."""


def _build_orchestrator(db):
    """Construct the Orchestrator with the composite retrieval + LLM adapters.

    Bound to the supplied DB session so persisted Card/CardSource rows are written.
    Imported lazily (and tolerantly) so app/ws import never hard-depends on packages that
    are still under construction. Tests monkeypatch this function to inject a deterministic
    in-memory Orchestrator. Raises :class:`_OrchestratorUnavailable` if a piece is missing.
    """
    try:
        from relay.adapters.llm_tfy import TfyLLMClient
        from relay.orchestrator.synth import Orchestrator
        from relay.retrieval.service import CompositeRetrievalService

        # Desk-mode per-customer memory (Moss-backed, built-in embeddings). Best-effort:
        # if it can't be built, proceed without memory (it's optional context, never grounding).
        memory = None
        try:
            from relay.memory.moss_memory import MossMemoryService

            memory = MossMemoryService()
        except Exception as exc:  # noqa: BLE001
            logger.info("memory service unavailable; Desk runs without it", extra={"error": str(exc)})

        return Orchestrator(
            retrieval=CompositeRetrievalService.from_settings(),
            llm=TfyLLMClient(),
            session=db,
            memory=memory,
        )
    except Exception as exc:  # noqa: BLE001 — missing creds / adapters under construction
        raise _OrchestratorUnavailable(str(exc)) from exc


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
            await _handle_query_manual(
                session_id=session_id,
                claims=claims,
                mode=state.get("mode", "live"),
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
    except AuthError as exc:
        logger.warning("ws token rejected", extra={"error": str(exc)})
        await websocket.close(code=WS_POLICY_VIOLATION)
        return

    # Publish claims so any DB access inside this connection is RLS-scoped.
    set_current_claims(claims)

    await websocket.accept()
    await hub.register(session_id, websocket)

    # Determine which retrieval backend is currently primary for the status event.
    backend = _current_retrieval_backend()
    await websocket.send_json(
        build_event(
            "session.status",
            {"status": "active", "retrieval_backend": backend},
            _now_iso(),
        )
    )

    state: dict[str, Any] = {"mode": "live"}
    try:
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
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # malformed frame, transport error
        logger.warning("ws loop error", extra={"error": str(exc), "session_id": session_id})
        try:
            await websocket.close(code=WS_INTERNAL_ERROR)
        except Exception:
            pass
    finally:
        await hub.unregister(session_id, websocket)


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


__all__ = ["WsHub", "hub", "get_hub", "router", "session_ws"]

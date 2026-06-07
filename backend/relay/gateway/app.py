"""FastAPI application factory for the Relay gateway.

:func:`create_app` wires:

* **CORS** locked to ``settings.cors_origins`` — the parsed ``FRONTEND_ORIGIN`` list
  (comma-separated, trailing slashes stripped; credentials allowed, never ``*``).
* All REST routers from ``relay.gateway.routes.*`` mounted under ``/api/v1`` by their
  module-level ``router`` symbol (documents, sessions, query, leads, account).
* The WebSocket router (``relay.gateway.ws.router``) mounted at the app root so its path
  is exactly ``/ws/sessions/{session_id}`` (the frozen contract — NOT under ``/api/v1``).
* ``GET /health`` -> ``{"status": "ok"}``.
* Exception handlers that emit the frozen error envelope ``{"error": {"code", "message"}}``
  using the API_SPEC error codes (``document_unsupported``, ``document_too_large``,
  ``session_not_found``, ``retrieval_unavailable``, ``internal_error``; ``no_grounding`` is
  represented as ``card: null`` by the routes, never as an error here).
* A combined per-org + per-IP rate-limit middleware (in-process token buckets) and a
  request-id / latency logging middleware.
* Pydantic ``RequestValidationError`` mapped to a ``422`` error envelope.

Routers are imported tolerantly: any route module not yet present (the package is built in
parallel) is skipped with a warning so the app — and therefore ``/health`` and the WS hub —
boots regardless. A module-level :data:`app` is exposed for ASGI servers.
"""

from __future__ import annotations

import importlib
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Iterable

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from relay.config import settings
from relay.logging import get_logger, set_request_id

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://lepmbgtxjduuoiwvdaww.supabase.co",                                    # local dev   # your vercel preview
        "https://api.riyanshomelab.com",
        "https://relay-omega-five.vercel.app",                      # prod when you have it
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger = get_logger("relay.gateway.app")

API_PREFIX = "/api/v1"

# REST route modules to mount under API_PREFIX. Each must expose a module-level ``router``.
_ROUTE_MODULES: tuple[str, ...] = (
    "relay.gateway.routes.documents",
    "relay.gateway.routes.sessions",
    "relay.gateway.routes.query",
    "relay.gateway.routes.leads",
    "relay.gateway.routes.customers",
    "relay.gateway.routes.account",
    "relay.gateway.routes.inbound",
)

# Rate-limit configuration (requests per window, in seconds). In-process buckets — adequate
# for a single-instance hackathon deploy; swap for Redis for horizontal scale.
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_RATE_LIMIT_PER_ORG = 600
_RATE_LIMIT_PER_IP = 300


# ---------------------------------------------------------------------------
# Error envelope helper
# ---------------------------------------------------------------------------


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Build the frozen ``{"error": {"code", "message"}}`` JSON response."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, time the request, and emit a structured access log.

    The request id is published on the logging contextvar so every log line within the
    request carries it, and echoed back on the ``X-Request-ID`` response header. Never logs
    bodies, secrets, or transcripts — only method, path, status, and latency.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:24]}"
        set_request_id(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
            logger.info(
                "request handled",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "latency_ms": latency_ms,
                },
            )
        response.headers["X-Request-ID"] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiting, per-org (from the verified JWT) and per-client-IP.

    The org id is taken ONLY from the verified-claims contextvar (never from a client-
    supplied value); if claims are not yet resolved for a request, only the per-IP bucket
    applies. ``/health`` and WebSocket upgrades are exempt. On limit breach, returns the
    frozen error envelope with HTTP 429 and code ``rate_limited``.
    """

    def __init__(self, app, *, window: float, per_org: int, per_ip: int) -> None:
        super().__init__(app)
        self._window = window
        self._per_org = per_org
        self._per_ip = per_ip
        self._org_hits: dict[str, deque[float]] = defaultdict(deque)
        self._ip_hits: dict[str, deque[float]] = defaultdict(deque)

    @staticmethod
    def _client_ip(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",", 1)[0].strip()
        return request.client.host if request.client else "unknown"

    def _allowed(self, bucket: deque[float], limit: int, now: float) -> bool:
        cutoff = now - self._window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Exempt health and WS upgrade handshakes from REST rate limiting.
        if path == "/health" or request.headers.get("upgrade", "").lower() == "websocket":
            return await call_next(request)

        now = time.monotonic()

        ip = self._client_ip(request)
        if not self._allowed(self._ip_hits[ip], self._per_ip, now):
            return _error_response(429, "rate_limited", "per-IP rate limit exceeded")

        # Per-org limit (only when claims are resolved). Read from the verified contextvar.
        try:
            from relay.auth.rls import get_current_claims

            claims = get_current_claims()
            org_id = getattr(claims, "org_id", None) if claims else None
        except Exception:
            org_id = None
        if org_id and not self._allowed(self._org_hits[str(org_id)], self._per_org, now):
            return _error_response(429, "rate_limited", "per-organization rate limit exceeded")

        return await call_next(request)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

# Map well-known application error codes to HTTP statuses (API_SPEC).
_CODE_STATUS: dict[str, int] = {
    "document_unsupported": 415,
    "document_too_large": 413,
    "session_not_found": 404,
    "retrieval_unavailable": 503,
    "internal_error": 500,
    "unauthorized": 401,
    "forbidden": 403,
    "rate_limited": 429,
    "validation_error": 422,
}


def _install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException) -> JSONResponse:
        # Handlers/deps that already raise the frozen envelope as ``detail`` pass through;
        # otherwise we synthesize one, mapping the status to a known code where possible.
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail, headers=exc.headers)
        code = next(
            (c for c, s in _CODE_STATUS.items() if s == exc.status_code),
            "internal_error" if exc.status_code >= 500 else "error",
        )
        message = detail if isinstance(detail, str) else "request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": message}},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(422, "validation_error", "request validation failed")

    @app.exception_handler(Exception)
    async def _unhandled_exc(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals or stack traces to clients; full detail goes to the log.
        logger.error("unhandled exception", extra={"path": request.url.path}, exc_info=exc)
        return _error_response(500, "internal_error", "an internal error occurred")


# ---------------------------------------------------------------------------
# Router wiring
# ---------------------------------------------------------------------------


def _iter_rest_routers(module_names: Iterable[str]):
    """Yield ``(module_name, router)`` for each importable route module exposing ``router``.

    Missing modules (built in parallel) are skipped with a warning so the app still boots.
    """
    for name in module_names:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError:
            logger.warning("route module not found; skipping", extra={"route_module": name})
            continue
        except Exception as exc:  # import-time error in a route module
            logger.error("failed importing route module", extra={"route_module": name, "error": str(exc)})
            continue
        router = getattr(module, "router", None)
        if router is None:
            logger.warning("route module has no 'router'; skipping", extra={"route_module": name})
            continue
        yield name, router


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _build_lifespan():
    """FastAPI lifespan: start/stop the Redis-backed WS hub so cards/transcripts
    broadcast by the separate agent process reach browser sockets on this gateway."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from relay.gateway.ws import hub, inbound_hub

        try:
            await hub.start_redis()
        except Exception as exc:  # noqa: BLE001 — degrade to local-only hub
            logger.warning("ws hub redis disabled", extra={"error": str(exc)})
        try:
            await inbound_hub.start_redis()
        except Exception as exc:  # noqa: BLE001 — degrade to local-only widget hub
            logger.warning("inbound ws hub redis disabled", extra={"error": str(exc)})
        # Prewarm the DB connection pool: open a handful of connections up front so the
        # remote Supabase TLS handshakes (~2s each, one-time per connection) happen at
        # startup — not mid-conversation where they'd hitch the inbound pipeline.
        try:
            import asyncio as _asyncio

            from sqlalchemy import text as _text

            from relay.db.base import async_session_maker

            async def _warm_one() -> None:
                async with async_session_maker() as s:
                    await s.execute(_text("SELECT 1"))

            await _asyncio.gather(*[_warm_one() for _ in range(5)], return_exceptions=True)
            logger.info("db pool prewarmed")
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning("db pool prewarm skipped", extra={"error": str(exc)})
        try:
            yield
        finally:
            try:
                await hub.stop_redis()
            except Exception:  # pragma: no cover
                pass
            try:
                await inbound_hub.stop_redis()
            except Exception:  # pragma: no cover
                pass

    return lifespan


def create_app() -> FastAPI:
    """Build and return the configured Relay FastAPI application."""
    app = FastAPI(
        title="Relay API",
        version="1.0.0",
        description="Relay ambient co-pilot gateway (REST + WebSocket).",
        lifespan=_build_lifespan(),
    )

    # --- CORS: locked to the configured frontend origin(s), credentials allowed.
    #     FRONTEND_ORIGIN may be a comma-separated list (e.g. a localhost dev origin
    #     plus the deployed frontend); settings.cors_origins parses it and strips any
    #     trailing slash. Never '*' with credentials. ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    # --- Rate limiting + request context. Note: middleware runs in reverse add order, so
    #     RequestContext (added last) wraps outermost and sets the request id first. ---
    app.add_middleware(
        RateLimitMiddleware,
        window=_RATE_LIMIT_WINDOW_SECONDS,
        per_org=_RATE_LIMIT_PER_ORG,
        per_ip=_RATE_LIMIT_PER_IP,
    )
    app.add_middleware(RequestContextMiddleware)

    # --- Exception handlers (frozen error envelope). ---
    _install_exception_handlers(app)

    # --- Health check. ---
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # --- REST routers under /api/v1. ---
    mounted: list[str] = []
    for name, router in _iter_rest_routers(_ROUTE_MODULES):
        app.include_router(router, prefix=API_PREFIX)
        mounted.append(name.rsplit(".", 1)[-1])
    logger.info("REST routers mounted", extra={"routers": mounted, "prefix": API_PREFIX})

    # --- WebSocket router at root (path is exactly /ws/sessions/{session_id}). ---
    from relay.gateway.ws import router as ws_router

    app.include_router(ws_router)

    return app


# Module-level ASGI app for `uvicorn relay.gateway.app:app`.
app = create_app()


__all__ = ["create_app", "app", "API_PREFIX"]

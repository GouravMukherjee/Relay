"""Structured JSON logging for Relay.

Every log record is emitted as a single JSON line containing at minimum:
  - ``ts``         — ISO-8601 UTC timestamp
  - ``level``      — log level name (INFO, WARNING, ERROR, …)
  - ``logger``     — logger hierarchy name
  - ``message``    — human-readable message
  - ``request_id`` — if set via :func:`set_request_id` for the current context
  - ``latency_ms`` — if passed to :func:`log_latency`

Usage::

    from relay.logging import get_logger, set_request_id, log_latency

    logger = get_logger(__name__)

    set_request_id("req_abc123")          # call from request middleware / dep
    logger.info("Processing document", extra={"document_id": "doc_xyz"})

    log_latency(logger, "retrieval", latency_ms=42.7, backend="moss")
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Context variable — per-request/task request ID injected by middleware.
# ---------------------------------------------------------------------------
_REQUEST_ID: ContextVar[str | None] = ContextVar("_REQUEST_ID", default=None)


def set_request_id(request_id: str | None) -> None:
    """Set the request ID for the current async context."""
    _REQUEST_ID.set(request_id)


def get_request_id() -> str | None:
    """Return the request ID for the current async context, or ``None``."""
    return _REQUEST_ID.get()


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a JSON object on a single line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Inject request_id if available in this context.
        rid = _REQUEST_ID.get()
        if rid:
            payload["request_id"] = rid

        # Propagate any extra fields the caller passed via ``extra={...}``.
        _STANDARD_ATTRS = {
            "args", "asctime", "created", "exc_info", "exc_text", "filename",
            "funcName", "levelname", "levelno", "lineno", "message", "module",
            "msecs", "msg", "name", "pathname", "process", "processName",
            "relativeCreated", "stack_info", "taskName", "thread", "threadName",
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------

_configured = False


def _configure_root_logger() -> None:
    """Configure the root logger to emit JSON to stdout exactly once."""
    global _configured
    if _configured:
        return
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a :class:`logging.Logger` configured for structured JSON output.

    Call once per module at module level::

        logger = get_logger(__name__)
    """
    _configure_root_logger()
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def log_latency(
    logger: logging.Logger,
    operation: str,
    *,
    latency_ms: float,
    level: int = logging.INFO,
    **extra: Any,
) -> None:
    """Emit a structured latency record.

    Example::

        log_latency(logger, "retrieval", latency_ms=42.1, backend="moss")

    Produces a JSON line like::

        {"ts": "...", "level": "INFO", "logger": "...",
         "message": "retrieval completed",
         "operation": "retrieval", "latency_ms": 42.1, "backend": "moss"}
    """
    logger.log(
        level,
        "%s completed",
        operation,
        extra={"operation": operation, "latency_ms": round(latency_ms, 3), **extra},
    )

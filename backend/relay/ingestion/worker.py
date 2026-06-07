"""arq ingestion worker.

This module provides:
- :data:`WorkerSettings` — arq worker configuration; run with
  ``arq relay.ingestion.worker.WorkerSettings``.
- :func:`enqueue_ingest` — helper to push a ``ingest_document`` job onto the
  Redis queue from the upload route (or any async caller).

The worker constructs its own instances of the three sponsor-backed adapters
(parser, embeddings, retrieval) using the module-level ``settings``.  Adapters
validate their required creds at construction time and raise ``RuntimeError`` if
a required key is absent — this surfaces misconfiguration immediately at worker
startup rather than silently failing at job execution time.

The arq ``ctx`` dict is populated once in :func:`startup` and torn down in
:func:`shutdown`; individual job functions receive it as their first argument.

All DB access uses :func:`~relay.db.base.privileged_session` (RLS bypassed),
and every query/write includes an explicit ``organization_id`` filter.  This
satisfies the architecture invariant "privileged scope with explicit org scoping".
"""

from __future__ import annotations

import os
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from relay.config import settings
from relay.ingestion.pipeline import ingest_document
from relay.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Job function(s)
# ---------------------------------------------------------------------------


async def job_ingest_document(ctx: dict[str, Any], document_id: str) -> None:
    """arq job: run the full ingestion pipeline for *document_id*.

    Args:
        ctx:          arq worker context (populated by :func:`startup`).
        document_id:  Prefixed document PK, e.g. ``"doc_abc123"``.
    """
    logger.info("arq job started", extra={"job": "ingest_document", "document_id": document_id})

    await ingest_document(
        document_id,
        parser=ctx["parser"],
        embeddings=ctx["embeddings"],
        retrieval=ctx["retrieval"],
        s3_client=ctx["s3"],
    )

    logger.info("arq job completed", extra={"job": "ingest_document", "document_id": document_id})


# ---------------------------------------------------------------------------
# Startup / shutdown lifecycle
# ---------------------------------------------------------------------------


async def startup(ctx: dict[str, Any]) -> None:
    """Initialise shared adapter singletons for all jobs in this worker process.

    Adapters are constructed here so that:
    * Required creds are validated at process start.
    * HTTP connection pools are reused across jobs (efficiency).
    * Tests can replace ctx entries with mocks.
    """
    logger.info("ingestion worker starting up")

    # Import adapters here (not at module level) so that importing this module
    # in tests never triggers adapter construction (no creds required).
    from relay.adapters.s3 import S3Storage  # noqa: PLC0415
    from relay.adapters.unsiloed import UnsiloedParser  # noqa: PLC0415
    from relay.adapters.embeddings_tfy import TfyEmbeddings  # noqa: PLC0415
    from relay.retrieval.service import CompositeRetrievalService  # noqa: PLC0415

    ctx["parser"] = UnsiloedParser(api_key=settings.unsiloed_api_key)
    ctx["embeddings"] = TfyEmbeddings(
        api_key=settings.tfy_api_key,
        gateway_url=settings.tfy_gateway_url,
    )
    ctx["retrieval"] = CompositeRetrievalService(
        moss_api_key=settings.moss_api_key,
        moss_base_url=settings.moss_base_url,
        # PgVectorRetrieval is constructed inside CompositeRetrievalService
        # and shares the module-level engine from relay.db.base.
    )
    ctx["s3"] = S3Storage(
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        aws_region=settings.aws_region,
        bucket=settings.s3_bucket,
    )

    logger.info("ingestion worker startup complete")


async def shutdown(ctx: dict[str, Any]) -> None:
    """Tear down adapter resources (HTTP sessions, connection pools)."""
    logger.info("ingestion worker shutting down")

    # Close any async HTTP clients held by adapters.
    for key in ("parser", "embeddings", "retrieval", "s3"):
        adapter = ctx.get(key)
        if adapter is not None:
            close = getattr(adapter, "aclose", None) or getattr(adapter, "close", None)
            if callable(close):
                try:
                    import inspect  # noqa: PLC0415

                    if inspect.iscoroutinefunction(close):
                        await close()
                    else:
                        close()
                except Exception as exc:
                    logger.warning(
                        "error closing adapter",
                        extra={"adapter": key, "error": str(exc)},
                    )

    logger.info("ingestion worker shutdown complete")


# ---------------------------------------------------------------------------
# arq WorkerSettings
# ---------------------------------------------------------------------------

_redis_settings = RedisSettings.from_dsn(settings.redis_url)


class WorkerSettings:
    """arq worker configuration.

    Run with::

        arq relay.ingestion.worker.WorkerSettings

    Or programmatically::

        from arq import run_worker
        from relay.ingestion.worker import WorkerSettings
        run_worker(WorkerSettings)
    """

    functions = [job_ingest_document]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings

    # Retry failed jobs up to 3 times with exponential back-off handled by arq.
    max_tries = 3

    # Keep job results in Redis for 24 h for debugging.
    keep_result = 86_400

    # Log job-level health info.
    health_check_interval = 60


# ---------------------------------------------------------------------------
# Enqueue helper
# ---------------------------------------------------------------------------


async def enqueue_ingest(document_id: str) -> str:
    """Push a ``job_ingest_document`` job onto the arq queue.

    Creates a **short-lived** Redis pool connection, enqueues the job, and
    closes the pool.  This is intentionally not a long-lived connection —
    callers (FastAPI route handlers) should not keep a persistent pool unless
    they manage its lifecycle at the application level.

    Args:
        document_id: Prefixed document PK, e.g. ``"doc_abc123"``.

    Returns:
        The arq job ID string.

    Raises:
        Exception: Propagates any Redis connection or enqueue error.
    """
    pool = await create_pool(_redis_settings)
    try:
        job = await pool.enqueue_job("job_ingest_document", document_id)
        job_id: str = job.job_id if job else "unknown"
        logger.info(
            "enqueued ingestion job",
            extra={"document_id": document_id, "job_id": job_id},
        )
        return job_id
    finally:
        await pool.aclose()

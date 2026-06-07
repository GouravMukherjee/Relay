"""Manual query (fallback / Desk) route.

Endpoints
---------
POST /query   Run a manual retrieval-grounded query via the Orchestrator.

Mounted under /api/v1 by create_app().
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from relay.auth.deps import current_claims
from relay.auth.jwt import Claims
from relay.db.base import get_session
from relay.db.models import Session
from relay.schemas.query import QueryRequest, QueryResponse

logger = logging.getLogger("relay.gateway.routes.query")

router = APIRouter(tags=["query"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_orchestrator(db: AsyncSession = Depends(get_session)):
    """FastAPI dependency that constructs the grounded :class:`Orchestrator`.

    Built with the production composite retrieval service (Moss primary +
    pgvector fallback) and the TFY-gateway LLM client, bound to the request's
    RLS-scoped DB session so persisted Card/CardSource rows are tenant-scoped.

    Overridable in tests via ``app.dependency_overrides[get_orchestrator]`` to
    inject deterministic in-memory fakes (no external creds required).
    """
    from relay.adapters.llm_tfy import TfyLLMClient
    from relay.orchestrator.synth import Orchestrator
    from relay.retrieval.service import CompositeRetrievalService

    retrieval = CompositeRetrievalService.from_settings()
    llm = TfyLLMClient()
    return Orchestrator(retrieval=retrieval, llm=llm, session=db)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post(
    "/query",
    response_model=QueryResponse,
    summary="Run a manual grounded query (Desk fallback or explicit query)",
)
async def manual_query(
    body: QueryRequest,
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
    orchestrator=Depends(get_orchestrator),
) -> QueryResponse:
    """Invoke the Orchestrator for a manual query.

    Returns ``{"card": Card}`` on success, or ``{"card": null}`` when no chunk
    is sufficiently relevant (grounded-or-silent contract).  This is always
    HTTP 200 — ``card: null`` is not an error.

    Raises 503 with ``retrieval_unavailable`` if retrieval/LLM services fail.
    """
    org_id = claims.org_id

    # Validate session exists if provided (RLS-scoped to this org).
    if body.session_id:
        sess = await db.get(Session, body.session_id)
        if sess is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": {
                        "code": "session_not_found",
                        "message": f"Session {body.session_id!r} not found",
                    }
                },
            )

    try:
        card_orm = await orchestrator.synthesize(
            session_id=body.session_id,
            org_id=org_id,
            mode=body.mode,
            query_text=body.text,
            customer_id=body.customer_id,
        )
    except Exception as exc:
        logger.error("Orchestrator.synthesize error: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "retrieval_unavailable",
                    "message": "Retrieval failed",
                }
            },
        ) from exc

    if card_orm is None:
        # Grounded-or-silent: no relevant chunks found.
        return QueryResponse(card=None)

    # The Orchestrator already returns a fully-built Card schema (answer, title,
    # and the cited Source list) and has persisted the Card + CardSource rows.
    # Return it directly — it is the frozen-contract response shape.
    card_schema = card_orm

    logger.info(
        "query.manual session_id=%s mode=%s org_id=%s latency_ms=%d sources=%d",
        body.session_id,
        body.mode,
        org_id,
        card_schema.latency_ms,
        len(card_schema.sources),
    )
    return QueryResponse(card=card_schema)

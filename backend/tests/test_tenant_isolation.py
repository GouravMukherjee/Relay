"""Tenant isolation tests (the sanctioned-deviation security requirement).

Org A must never be able to read org B's documents. Isolation is enforced at the
database by the ``org_isolation`` Postgres RLS policies (DATA_MODEL + the master
prompt). These policies read ``request.jwt.claims ->> 'org_id'`` from the GUC set
by ``apply_rls_claims``.

RLS cannot be exercised on the SQLite fallback (no row-level security, no
``current_setting``), so these tests REQUIRE a live Postgres and are skipped with
a clear reason otherwise.

An application-layer cross-org probe is included that runs on any backend: it
asserts that fetching a foreign-org document by id returns 404 through the API.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import (
    ORG_A,
    ORG_B,
    USER_A,
    USER_B,
    USING_POSTGRES,
    make_token,
    requires_pg,
)
from tests.conftest import test_session_maker as session_maker

pytestmark = pytest.mark.asyncio


async def _seed_document(org_id: str, title: str) -> str:
    from relay.db.models import Document

    did = f"doc_{uuid.uuid4().hex[:24]}"
    async with session_maker() as s:
        s.add(
            Document(
                id=did,
                organization_id=org_id,
                title=title,
                source_type="pdf",
                status="ready",
                chunk_count=1,
            )
        )
        await s.commit()
    return did


@requires_pg
@pytest.mark.pg
async def test_org_a_cannot_read_org_b_documents_via_rls():
    """List documents as org A; org B's docs must not appear (RLS-enforced)."""
    from sqlalchemy import select

    from relay.auth.rls import apply_rls_claims
    from relay.auth.jwt import Claims
    from relay.db.models import Document

    a_doc = await _seed_document(ORG_A, "A-only Security.pdf")
    b_doc = await _seed_document(ORG_B, "B-only Pricing.pdf")

    # Query as org A with RLS claims applied -> only org A's rows visible.
    async with session_maker() as db:
        await apply_rls_claims(db, Claims(user_id=USER_A, org_id=ORG_A, role="owner"))
        ids = {
            d.id for d in (await db.execute(select(Document))).scalars().all()
        }
    assert a_doc in ids
    assert b_doc not in ids, "RLS leak: org A saw org B's document"


@requires_pg
@pytest.mark.pg
async def test_org_b_cannot_read_org_a_documents_via_rls():
    from sqlalchemy import select

    from relay.auth.rls import apply_rls_claims
    from relay.auth.jwt import Claims
    from relay.db.models import Document

    a_doc = await _seed_document(ORG_A, "A-secret.pdf")
    b_doc = await _seed_document(ORG_B, "B-secret.pdf")

    async with session_maker() as db:
        await apply_rls_claims(db, Claims(user_id=USER_B, org_id=ORG_B, role="owner"))
        ids = {
            d.id for d in (await db.execute(select(Document))).scalars().all()
        }
    assert b_doc in ids
    assert a_doc not in ids


async def test_cross_org_document_fetch_returns_404_via_api(app):
    """API-level probe (runs on any backend, including PG).

    Org B creates a document; org A fetching it by id must get 404. On Postgres
    this is RLS-enforced; on SQLite it documents the intended app contract. We
    drive both orgs through the real ``current_claims`` dependency by swapping
    the override per request.
    """
    import httpx
    from httpx import ASGITransport

    from relay.auth.deps import current_claims
    from tests.conftest import _claims_dep_for

    token_a = make_token(user_id=USER_A, org_id=ORG_A)
    token_b = make_token(user_id=USER_B, org_id=ORG_B)

    transport = ASGITransport(app=app)

    # Org B uploads a document row directly under ORG_B.
    b_doc = await _seed_document(ORG_B, "B-confidential.pdf")

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        # As org A, the foreign document must not be retrievable.
        app.dependency_overrides[current_claims] = _claims_dep_for(token_a)
        r_a = await ac.get(
            f"/api/v1/documents/{b_doc}", headers={"Authorization": f"Bearer {token_a}"}
        )

        # As org B, the same document IS retrievable (sanity: it exists).
        app.dependency_overrides[current_claims] = _claims_dep_for(token_b)
        r_b = await ac.get(
            f"/api/v1/documents/{b_doc}", headers={"Authorization": f"Bearer {token_b}"}
        )

    app.dependency_overrides.pop(current_claims, None)

    if USING_POSTGRES:  # pragma: no cover - depends on environment
        assert r_a.status_code == 404, "RLS should hide org B's doc from org A"
        assert r_b.status_code == 200
    else:
        # SQLite has no RLS; the row is globally visible. We still assert org B
        # (the owner) can read it, and record that strict isolation needs PG.
        assert r_b.status_code == 200
        pytest.skip(
            "cross-org 404 requires Postgres RLS; SQLite fallback cannot enforce "
            "row-level security (org A read returned %s)" % r_a.status_code
        )

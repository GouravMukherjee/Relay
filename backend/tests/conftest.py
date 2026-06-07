"""Pytest fixtures for the Relay backend test-suite.

Goals (per docs/TEST_PLAN.md + the task contract):

* Exercise the FastAPI gateway (``create_app()``) end-to-end via an httpx
  ``AsyncClient`` mounted on the ASGI app — no network, no live services.
* Run with **NO external credentials**: deterministic in-memory fakes are
  injected for the four sponsor interfaces (RetrievalService, Embeddings,
  DocumentParser, LLMClient) through FastAPI ``dependency_overrides`` and
  module-level monkeypatching.
* Use a real-but-ephemeral database. A live Postgres is preferred (it is the
  only place the ``org_isolation`` RLS policies can actually be enforced); when
  no Postgres is reachable we fall back to an in-memory SQLite engine so the
  contract / grounding / latency / WS suites still run. RLS-dependent tests are
  marked ``pg`` and skip on the SQLite fallback with a clear reason.

The HS256 JWT helper mints tokens signed with ``settings.supabase_jwt_secret``
(the sanctioned test path through ``relay.auth.jwt.verify_token``) for two
distinct orgs A and B.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Environment MUST be set before any relay.* import so settings pick it up.
# ---------------------------------------------------------------------------

TEST_JWT_SECRET = "relay-test-hs256-secret-key-0123456789"
os.environ.setdefault("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
# Keep the app's CORS / WS origin check happy for the WS handshake (no Origin
# header is sent by the test client, so this is mostly belt-and-braces).
os.environ.setdefault("FRONTEND_ORIGIN", "http://localhost:5173")

import jwt as pyjwt  # noqa: E402

# Rebuild the cached settings singleton so SUPABASE_JWT_SECRET is honoured.
from relay import config as relay_config  # noqa: E402

relay_config.get_settings.cache_clear()
relay_config.settings = relay_config.get_settings()
settings = relay_config.settings


# ---------------------------------------------------------------------------
# Teach SQLite to render the Postgres-specific column types as benign
# equivalents so Base.metadata.create_all() works on the SQLite fallback.
# (No effect on a real Postgres engine.)
# ---------------------------------------------------------------------------

import sqlalchemy.types as _satypes  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB, UUID  # noqa: E402
from pgvector.sqlalchemy import Vector  # noqa: E402


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):  # pragma: no cover - DDL shim
    return "JSON"


@compiles(Vector, "sqlite")
def _compile_vector(type_, compiler, **kw):  # pragma: no cover - DDL shim
    return "TEXT"


@compiles(UUID, "sqlite")
def _compile_uuid(type_, compiler, **kw):  # pragma: no cover - DDL shim
    return "VARCHAR(36)"


@compiles(_satypes.ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):  # pragma: no cover - DDL shim
    return "JSON"


def _sqlite_jsonify_list_columns() -> None:
    """On SQLite, store ARRAY/Vector columns as JSON so Python lists bind cleanly.

    SQLite's DBAPI cannot bind a Python ``list`` directly; the DDL shims above
    let the tables be *created*, but inserts of list values (Document.tags,
    Chunk.embedding) still fail. Swap those column types to a JSON-backed
    TypeDecorator for the test engine only. No effect on a real Postgres run.
    """
    import json as _json

    from sqlalchemy import JSON
    from sqlalchemy.types import TypeDecorator

    class _JsonList(TypeDecorator):
        impl = JSON
        cache_ok = True

        def process_bind_param(self, value, dialect):
            return list(value) if value is not None else value

        def process_result_value(self, value, dialect):
            if isinstance(value, str):
                try:
                    return _json.loads(value)
                except Exception:
                    return value
            return value

    for table in Base.metadata.tables.values():
        for col in table.columns:
            tname = type(col.type).__name__
            if tname in ("ARRAY", "Vector"):
                col.type = _JsonList()


# ---------------------------------------------------------------------------
# Database engine selection: prefer live Postgres, else SQLite fallback.
# ---------------------------------------------------------------------------

import sqlalchemy  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from relay.db import base as db_base  # noqa: E402
from relay.db.base import Base  # noqa: E402
import relay.db.models  # noqa: E402,F401 - register all tables on Base.metadata


def _maybe_jsonify_for_sqlite(url: str) -> None:
    if url.startswith("sqlite"):
        _sqlite_jsonify_list_columns()


def _probe_postgres(url: str) -> bool:
    async def _check() -> bool:
        try:
            eng = create_async_engine(url)
            async with eng.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await eng.dispose()
            return True
        except Exception:
            return False

    try:
        return asyncio.new_event_loop().run_until_complete(_check())
    except Exception:
        return False


_PG_URL = settings.database_url
USING_POSTGRES = _PG_URL.startswith("postgresql") and _probe_postgres(_PG_URL)

if USING_POSTGRES:  # pragma: no cover - depends on environment
    TEST_DB_URL = _PG_URL
    _connect_args: dict = {}
else:
    TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
    _connect_args = {"check_same_thread": False}

# A single shared engine for the whole test session. For the in-memory SQLite
# case we use a StaticPool so every connection sees the same database.
if TEST_DB_URL.startswith("sqlite"):
    from sqlalchemy.pool import StaticPool

    test_engine = create_async_engine(
        TEST_DB_URL,
        connect_args=_connect_args,
        poolclass=StaticPool,
    )
else:  # pragma: no cover
    test_engine = create_async_engine(TEST_DB_URL)

test_session_maker = async_sessionmaker(test_engine, expire_on_commit=False)

# Swap list-typed columns to JSON on SQLite so list binds work.
_maybe_jsonify_for_sqlite(TEST_DB_URL)

if TEST_DB_URL.startswith("sqlite"):
    # SQLite has no `set_config`/`current_setting` (the Postgres RLS GUC funcs
    # that apply_rls_claims uses). Register a no-op `set_config` so the request
    # path runs unchanged. RLS itself cannot be enforced here -> isolation tests
    # are PG-only (see `requires_pg`).
    from sqlalchemy import event

    @event.listens_for(test_engine.sync_engine, "connect")
    def _register_sqlite_shims(dbapi_conn, _rec):  # pragma: no cover - infra shim
        try:
            dbapi_conn.create_function("set_config", 3, lambda *_a: "")
            dbapi_conn.create_function("current_setting", 2, lambda *_a: None)
        except Exception:
            pass

# Rebind the application's engine + session maker so BOTH get_session (request
# path) and privileged_session (WS / worker path) use the test database.
db_base.engine = test_engine
db_base.async_session_maker = test_session_maker


# ---------------------------------------------------------------------------
# Two test organisations + users.
# ---------------------------------------------------------------------------

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())
USER_A = str(uuid.uuid4())
USER_B = str(uuid.uuid4())


def make_token(*, user_id: str, org_id: str, role: str = "owner", email: str | None = None) -> str:
    """Mint a valid HS256 Supabase-style access token for the test secret.

    Verified by ``relay.auth.jwt.verify_token`` via the HS256 branch (enabled by
    ``settings.supabase_jwt_secret``). Claims carry ``sub``/``org_id``/role.
    """
    from relay.auth import jwt as _jwtmod

    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": user_id,
        "org_id": org_id,
        "role": "authenticated",
        "app_metadata": {"org_id": org_id, "app_role": role},
        "email": email or f"{user_id[:8]}@example.com",
        "aud": "authenticated",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    # Match the configured issuer so PyJWT's `iss` verification passes when
    # supabase_url is set (placeholder .env). Harmless when issuer is None.
    issuer = _jwtmod._issuer()
    if issuer:
        payload["iss"] = issuer
    return pyjwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


@pytest.fixture(scope="session")
def token_a() -> str:
    return make_token(user_id=USER_A, org_id=ORG_A, email="owner-a@acme.test")


@pytest.fixture(scope="session")
def token_b() -> str:
    return make_token(user_id=USER_B, org_id=ORG_B, email="owner-b@globex.test")


@pytest.fixture(scope="session")
def auth_a(token_a: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_a}"}


@pytest.fixture(scope="session")
def auth_b(token_b: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_b}"}


# ---------------------------------------------------------------------------
# Deterministic in-memory fakes for the sponsor interfaces.
# ---------------------------------------------------------------------------

from relay.interfaces.embeddings import Embeddings  # noqa: E402
from relay.interfaces.llm import CardDraft, LLMClient  # noqa: E402
from relay.interfaces.parser import DocumentParser, ParsedBlock, ParsedDoc  # noqa: E402
from relay.interfaces.retrieval import (  # noqa: E402
    RetrievalResult,
    RetrievalService,
    RetrievedChunk,
)

# A tiny deterministic "knowledge base". A query that shares a keyword with a
# chunk retrieves it; an off-topic query retrieves nothing (-> no grounding).
FAKE_CHUNKS: list[RetrievedChunk] = [
    RetrievedChunk(
        chunk_id="chk_sla_0001",
        document_id="doc_security_0001",
        title="Security_Whitepaper.pdf",
        text="Relay guarantees a 99.9% uptime SLA backed by multi-region failover.",
        snippet="99.9% uptime SLA backed by multi-region failover.",
        score=0.91,
        moss_ref="moss://chk_sla_0001",
    ),
    RetrievedChunk(
        chunk_id="chk_price_0001",
        document_id="doc_pricing_0001",
        title="Pricing.pdf",
        text="The Growth plan is priced at $499/month and includes 10 seats.",
        snippet="Growth plan is priced at $499/month and includes 10 seats.",
        score=0.88,
        moss_ref="moss://chk_price_0001",
    ),
]
_FAKE_BY_ID = {c.chunk_id: c for c in FAKE_CHUNKS}


class FakeRetrieval(RetrievalService):
    """Keyword-overlap retrieval over FAKE_CHUNKS. Off-topic query -> [] chunks."""

    backend = "moss"

    def __init__(self) -> None:
        self.indexed: dict[str, RetrievedChunk] = dict(_FAKE_BY_ID)

    async def query(self, org_id: str, text: str, k: int = 5) -> RetrievalResult:
        q = (text or "").lower()
        hits: list[RetrievedChunk] = []
        for chunk in self.indexed.values():
            words = {w for w in chunk.text.lower().replace(".", " ").replace(",", " ").split() if len(w) > 3}
            qwords = {w for w in q.replace("?", " ").replace(".", " ").split() if len(w) > 3}
            if words & qwords:
                hits.append(chunk)
        hits.sort(key=lambda c: c.score, reverse=True)
        return RetrievalResult(chunks=hits[:k], backend="moss")

    async def index(self, chunks: list[RetrievedChunk]) -> None:
        for c in chunks:
            self.indexed[c.chunk_id] = c

    async def delete(self, document_id: str) -> None:
        for cid in [c.chunk_id for c in self.indexed.values() if c.document_id == document_id]:
            self.indexed.pop(cid, None)


class FakeEmbeddings(Embeddings):
    """Deterministic 1024-d hash embedding — no external creds."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            seed = abs(hash(t)) % (10**8)
            vec = [((seed >> (i % 31)) & 1) * 0.001 + (i % 7) * 0.0001 for i in range(1024)]
            out.append(vec)
        return out


class FakeParser(DocumentParser):
    """Decodes raw bytes as UTF-8 text into a single block."""

    async def parse(self, raw: bytes, content_type: str, filename: str | None = None) -> ParsedDoc:
        text = raw.decode("utf-8", errors="ignore")
        return ParsedDoc(text=text, blocks=[ParsedBlock(text=text, kind="text")])


class FakeLLM(LLMClient):
    """Deterministic grounded synthesis.

    Honours the grounding contract: returns ``None`` when given no chunks, and
    only ever cites chunk ids that were actually provided (the first chunk).
    """

    async def synthesize_card(
        self,
        *,
        query: str,
        chunks: list[RetrievedChunk],
        mode: str,
        window: list[str] | None = None,
    ) -> CardDraft | None:
        if not chunks:
            return None
        top = chunks[0]
        return CardDraft(
            answer=top.snippet,
            title=top.title,
            used_chunk_ids=[top.chunk_id],
        )


@pytest.fixture
def fake_retrieval() -> FakeRetrieval:
    return FakeRetrieval()


@pytest.fixture
def fake_embeddings() -> FakeEmbeddings:
    return FakeEmbeddings()


@pytest.fixture
def fake_parser() -> FakeParser:
    return FakeParser()


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


# ---------------------------------------------------------------------------
# DB schema lifecycle + per-test seed.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_schema():
    """Create all tables once for the test session (and the vector ext on PG)."""
    async with test_engine.begin() as conn:
        if USING_POSTGRES:  # pragma: no cover
            try:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            except Exception:
                pass
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest.fixture(autouse=True)
def _patch_side_effects(monkeypatch):
    """Neutralise external side-effects the routes trigger lazily.

    The documents route enqueues an arq job (Redis) and writes to S3; the leads
    route pings Slack; sessions mint LiveKit tokens. None are available without
    creds and arq's connect retries add ~25s of latency. Patch them to no-ops so
    the suite is fast, deterministic, and credential-free. The routes already
    treat these as best-effort (wrapped in try/except), so this matches prod
    behaviour when creds are absent — just faster.
    """

    async def _noop_enqueue(document_id: str) -> str:
        return f"job_{document_id}"

    monkeypatch.setattr("relay.ingestion.worker.enqueue_ingest", _noop_enqueue, raising=False)

    class _NoopS3:
        async def put_object(self, *a, **k):
            return None

        async def delete(self, *a, **k):
            return None

        def presigned_put_url(self, *a, **k):
            return "https://s3.test/presigned"

    monkeypatch.setattr("relay.adapters.s3.S3Storage", _NoopS3, raising=False)
    yield


@pytest_asyncio.fixture(autouse=True)
async def _seed_orgs():
    """Ensure both orgs + an owner user each exist before every test.

    Idempotent: the auth bootstrap also creates rows, but pre-seeding keeps the
    /me and /users endpoints deterministic.
    """
    from relay.db.models import Organization, OrgMembership, User

    async with test_session_maker() as s:
        for oid, uid, name in (
            (ORG_A, USER_A, "Owner A"),
            (ORG_B, USER_B, "Owner B"),
        ):
            existing = await s.get(Organization, oid)
            if existing is None:
                s.add(Organization(id=oid, name=f"Org {name}"))
            if await s.get(User, uid) is None:
                s.add(User(id=uid, organization_id=oid, name=name, role="owner", email=f"{name}@x.test"))
        await s.commit()
    yield


# ---------------------------------------------------------------------------
# App + client with dependency overrides for deterministic, credential-free runs.
# ---------------------------------------------------------------------------

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from relay.auth.jwt import Claims  # noqa: E402
from relay.auth.rls import set_current_claims  # noqa: E402


def _claims_dep_for(token: str):
    """Build a current_claims override that verifies a token and sets the RLS contextvar.

    On SQLite we cannot run the auth bootstrap (it uses privileged_session +
    control-plane writes that we already seed), so we verify the token and trust
    its org_id/role claims directly — matching what the real bootstrap converges to.
    """

    async def _dep() -> Claims:
        from relay.auth.jwt import verify_token

        claims = await verify_token(token)
        set_current_claims(claims)
        return claims

    return _dep


@pytest_asyncio.fixture
async def app(fake_retrieval, fake_llm):
    """Build the FastAPI app with sponsor fakes wired in.

    The Orchestrator dependency (query route) is overridden to use the fake
    retrieval + fake LLM bound to a fresh RLS-scoped session. The WS path's
    orchestrator factory is monkeypatched in the ws test itself.
    """
    from relay.gateway.app import create_app
    from relay.gateway.routes.query import get_orchestrator
    from relay.db.base import get_session
    from relay.orchestrator.synth import Orchestrator

    application = create_app()

    # Override the orchestrator builder to inject the fakes, bound to the
    # request's DB session (so Card/CardSource rows persist for read-back).
    async def _override_get_orchestrator():
        agen = get_session()
        session = await agen.__anext__()
        try:
            yield Orchestrator(retrieval=fake_retrieval, llm=fake_llm, session=session)
        finally:
            await agen.aclose()

    application.dependency_overrides[get_orchestrator] = _override_get_orchestrator
    return application


@pytest_asyncio.fixture
async def client(app, token_a):
    """AsyncClient bound to the app, authenticated as org A by default."""
    from relay.auth.deps import current_claims

    app.dependency_overrides[current_claims] = _claims_dep_for(token_a)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers.update({"Authorization": f"Bearer {token_a}"})
        yield ac
    app.dependency_overrides.pop(current_claims, None)


@pytest_asyncio.fixture
async def client_b(app, token_b):
    """AsyncClient authenticated as org B (for isolation tests)."""
    from relay.auth.deps import current_claims

    # Note: a single app instance can only hold one current_claims override at a
    # time; this fixture is used in isolation-specific tests that build their own
    # override per request. Here we default it to B.
    app.dependency_overrides[current_claims] = _claims_dep_for(token_b)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers.update({"Authorization": f"Bearer {token_b}"})
        yield ac
    app.dependency_overrides.pop(current_claims, None)


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "pg: test requires a live Postgres with RLS (skipped on SQLite fallback)."
    )


requires_pg = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="no live Postgres reachable (DATABASE_URL auth failed); RLS cannot be enforced on SQLite",
)

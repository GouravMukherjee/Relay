"""Northwind demo seed for Relay.

Seeds the demo organisation with:

Knowledge-base documents (from frontend/src/mock/dataset.ts KNOWLEDGE_BASE):
  - MSA.pdf              (doc_msa)
  - Security_Whitepaper.pdf  (doc_security)
  - Battlecard.pdf       (doc_battlecard)
  - Pricing.pdf          (doc_pricing)
  - Infrastructure_Map.pdf   (doc_infra)
  - FAQ.pdf              (doc_faq)
  - Ticket #1023         (doc_ticket_1023)  -- Desk support ticket

Customer (DESK_CUSTOMER):
  Sarah Chen / Acme Corp / Growth Plan
  Memory: previous CRM sync ticket resolved Mar 12

Lead (INTAKE_LEAD):
  Jordan Mraz / Brightwave Inc. / vp.eng@brightwave.io
  Qualifiers: budget $40-60k/yr, timeline this quarter, need = onboarding latency

All operations are idempotent: running the script twice leaves the DB unchanged.
The seed uses the privileged DB session (bypasses RLS) and scopes every row to
``settings.default_org_id``.

CLI flags
---------
--fake-embeddings   Use deterministic local hash embeddings (dim 1024) instead of
                    calling the external embeddings service. Safe for use without
                    external credentials.
--org-id UUID       Override the target organisation UUID (default: settings.default_org_id).
--reset             Drop all seeded documents / chunks before re-inserting.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import math
import struct
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, update

from relay.config import settings
from relay.db.base import privileged_session
from relay.db.models import (
    AuditLog,
    Chunk,
    Customer,
    Document,
    Lead,
    Memory,
    Organization,
    Session,
)
from relay.ids import new_id

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fixed demo IDs (match frontend dataset.ts so WS events reference the same IDs)
# ---------------------------------------------------------------------------

DEMO_DOC_IDS: dict[str, str] = {
    "doc_msa": "doc_msa",
    "doc_security": "doc_security",
    "doc_pricing": "doc_pricing",
    "doc_battlecard": "doc_battlecard",
    "doc_faq": "doc_faq",
    "doc_infra": "doc_infra",
    "doc_ticket_1023": "doc_ticket_1023",
}

# Fixed customer id for Sarah Chen so desk sessions can reference her.
DEMO_CUSTOMER_ID = "cus_sarah_chen_acme_demo_000000"

# Fixed intake session id for the example lead.
DEMO_INTAKE_SESSION_ID = "ses_intake_demo_jordan_00000000"

# ---------------------------------------------------------------------------
# Document definitions (title, source_type, chunks)
# ---------------------------------------------------------------------------


@dataclass
class SeedChunk:
    text: str
    ordinal: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SeedDocument:
    doc_id: str
    title: str
    source_type: str
    tags: list[str]
    chunks: list[SeedChunk]


# The snippet texts are taken verbatim from dataset.ts sources[].snippet so
# that pgvector queries return the exact content the frontend mock card cites.
SEED_DOCUMENTS: list[SeedDocument] = [
    SeedDocument(
        doc_id="doc_msa",
        title="MSA.pdf",
        source_type="pdf",
        tags=["legal", "contract", "sla"],
        chunks=[
            SeedChunk(
                ordinal=0,
                text=(
                    "Service availability of 99.9% measured monthly. "
                    "Service credits apply for downtime exceeding the SLA threshold. "
                    "Customer data deleted within 30 days of termination."
                ),
            ),
            SeedChunk(
                ordinal=1,
                text=(
                    "This Master Services Agreement governs access to the Relay platform. "
                    "Relay maintains financial backing for SLA credits — customers receive "
                    "service credits proportional to the duration of excess downtime."
                ),
            ),
            SeedChunk(
                ordinal=2,
                text=(
                    "Uptime is measured as the percentage of minutes in a calendar month "
                    "during which the production service is available. Scheduled maintenance "
                    "windows are excluded from the uptime calculation."
                ),
            ),
        ],
    ),
    SeedDocument(
        doc_id="doc_security",
        title="Security_Whitepaper.pdf",
        source_type="pdf",
        tags=["security", "compliance", "encryption"],
        chunks=[
            SeedChunk(
                ordinal=0,
                text=(
                    "SOC 2 Type II. All data encrypted at rest (AES-256) and in transit "
                    "(TLS 1.3). SSO (SAML) on Enterprise."
                ),
            ),
            SeedChunk(
                ordinal=1,
                text=(
                    "Relay undergoes annual third-party penetration testing. "
                    "Vulnerability disclosures are triaged within 24 hours and critical "
                    "patches released within 72 hours."
                ),
            ),
            SeedChunk(
                ordinal=2,
                text=(
                    "Access control follows the principle of least privilege. Role-based "
                    "access control (RBAC) is enforced at the API layer, with row-level "
                    "security (RLS) enforced at the database layer for tenant isolation."
                ),
            ),
            SeedChunk(
                ordinal=3,
                text=(
                    "Single Sign-On (SSO) via SAML 2.0 is available on the Enterprise tier. "
                    "OAuth 2.0 / OIDC is supported for all tiers. MFA is enforced for "
                    "all administrative accounts."
                ),
            ),
        ],
    ),
    SeedDocument(
        doc_id="doc_pricing",
        title="Pricing.pdf",
        source_type="pdf",
        tags=["pricing", "billing", "plans"],
        chunks=[
            SeedChunk(
                ordinal=0,
                text=(
                    "Starter $49/seat/mo · Growth $99/seat/mo · Enterprise custom. "
                    "Annual billing: 15% discount."
                ),
            ),
            SeedChunk(
                ordinal=1,
                text=(
                    "Starter tier includes up to 5 seats, 3 modes (Live, Desk, Intake), "
                    "and 10 GB document storage. Growth adds unlimited seats and priority "
                    "support. Enterprise is custom-priced and includes dedicated SLAs."
                ),
            ),
            SeedChunk(
                ordinal=2,
                text=(
                    "Volume discounts are available for organisations with more than 50 seats. "
                    "Multi-year contracts receive an additional 5% discount on top of the "
                    "annual billing discount."
                ),
            ),
        ],
    ),
    SeedDocument(
        doc_id="doc_battlecard",
        title="Battlecard.pdf",
        source_type="pdf",
        tags=["competitive", "sales"],
        chunks=[
            SeedChunk(
                ordinal=0,
                text=(
                    "vs Acme: Acme is post-call only. Relay wins on real-time surfacing "
                    "+ grounded citations during the call."
                ),
            ),
            SeedChunk(
                ordinal=1,
                text=(
                    "vs Acme: Acme summarises after the conversation ends; reps cannot "
                    "act on insights mid-call. Relay surfaces answers in under 500 ms "
                    "so reps can respond immediately."
                ),
            ),
            SeedChunk(
                ordinal=2,
                text=(
                    "Relay differentiator: every answer is grounded in the customer's own "
                    "indexed documents. No hallucination. Competitors that use generic LLMs "
                    "without retrieval cannot provide cited, verifiable answers."
                ),
            ),
        ],
    ),
    SeedDocument(
        doc_id="doc_faq",
        title="FAQ.pdf",
        source_type="pdf",
        tags=["faq", "support"],
        chunks=[
            SeedChunk(
                ordinal=0,
                text=(
                    "If CRM sync stalls, re-authenticate the integration from "
                    "Settings → Integrations."
                ),
            ),
            SeedChunk(
                ordinal=1,
                text=(
                    "How do I add a new document? Navigate to the Documents tab, click "
                    "Upload, and select a PDF, DOCX, or TXT file. Ingestion typically "
                    "completes within 2 minutes."
                ),
            ),
            SeedChunk(
                ordinal=2,
                text=(
                    "What file formats are supported? PDF, DOCX, TXT, and XLSX. Maximum "
                    "file size is 50 MB. For larger documents, split the file before uploading."
                ),
            ),
            SeedChunk(
                ordinal=3,
                text=(
                    "How is tenant data isolated? Every customer's data is stored in "
                    "dedicated schema rows with row-level security enforced at the Postgres "
                    "layer. No cross-tenant queries are possible."
                ),
            ),
        ],
    ),
    SeedDocument(
        doc_id="doc_infra",
        title="Infrastructure_Map.pdf",
        source_type="pdf",
        tags=["infrastructure", "reliability"],
        chunks=[
            SeedChunk(
                ordinal=0,
                text=(
                    "14 regions, active-active failover. Regional outage isolation."
                ),
            ),
            SeedChunk(
                ordinal=1,
                text=(
                    "Relay deploys across 14 global regions: US-East, US-West, EU-West, "
                    "EU-Central, AP-Southeast, AP-Northeast, and 8 additional edge regions. "
                    "Active-active failover ensures zero-downtime regional failover."
                ),
            ),
            SeedChunk(
                ordinal=2,
                text=(
                    "Each region runs independent Postgres replicas with synchronous "
                    "replication within the region and asynchronous replication across "
                    "regions. RPO is under 5 seconds; RTO is under 30 seconds."
                ),
            ),
        ],
    ),
    SeedDocument(
        doc_id="doc_ticket_1023",
        title="Ticket #1023",
        source_type="txt",
        tags=["support", "crm", "desk"],
        chunks=[
            SeedChunk(
                ordinal=0,
                text=(
                    "CRM export sync failing. Resolved via OAuth re-auth on Growth tier (Mar 12)."
                ),
                metadata={"ticket_id": "1023", "resolved_at": "2026-03-12"},
            ),
            SeedChunk(
                ordinal=1,
                text=(
                    "Customer reported that the Salesforce CRM export sync had stopped "
                    "updating. Root cause: OAuth token expiry after 90 days. Resolution: "
                    "customer re-authenticated the Salesforce integration from "
                    "Settings → Integrations → Salesforce → Re-authenticate."
                ),
                metadata={"ticket_id": "1023", "resolved_at": "2026-03-12"},
            ),
        ],
    ),
]

# ---------------------------------------------------------------------------
# Fake (deterministic) embeddings — no external creds required
# ---------------------------------------------------------------------------


def _fake_embedding(text: str, dim: int = 1024) -> list[float]:
    """Produce a deterministic unit-normalised vector from the text's SHA-256 hash.

    The hash is expanded to `dim` floats by repeating/truncating a seeded
    cosine pattern derived from the first 8 bytes of the digest.  The result
    is L2-normalised so cosine similarity works correctly.

    This is intentionally NOT semantically meaningful — it is only for local
    seeding and tests that need valid-length vectors without an embeddings API.
    """
    digest = hashlib.sha256(text.encode()).digest()
    # Use first 8 bytes as a seed for stable expansion.
    seed_int = struct.unpack(">Q", digest[:8])[0]
    rng_state = seed_int

    raw: list[float] = []
    for i in range(dim):
        # xorshift64 to advance state
        rng_state ^= rng_state << 13
        rng_state &= 0xFFFFFFFFFFFFFFFF
        rng_state ^= rng_state >> 7
        rng_state ^= rng_state << 17
        rng_state &= 0xFFFFFFFFFFFFFFFF
        # Map to [-1, 1] using the digest byte for extra variance
        byte_val = digest[i % 32]
        float_val = ((rng_state % 1000) / 500.0 - 1.0) * (0.5 + byte_val / 512.0)
        raw.append(float_val)

    # L2 normalise
    magnitude = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / magnitude for v in raw]


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _ensure_demo_org(session: Any, org_id: str) -> None:
    """Create the demo organisation row if it does not exist."""
    result = await session.execute(
        select(Organization).where(Organization.id == org_id)
    )
    if result.scalar_one_or_none() is None:
        session.add(
            Organization(
                id=org_id,
                name="Relay Demo Org",
            )
        )
        await session.flush()
        logger.info("Created demo organisation %s", org_id)
    else:
        logger.debug("Demo organisation %s already exists", org_id)


async def _seed_document(
    session: Any,
    doc: SeedDocument,
    org_id: str,
    fake_embeddings: bool,
    reset: bool,
) -> int:
    """Upsert one document and its chunks. Returns number of chunks written."""
    # Idempotency: if reset=True or doc already exists, delete existing chunks first.
    existing = await session.execute(
        select(Document).where(Document.id == doc.doc_id)
    )
    existing_doc = existing.scalar_one_or_none()

    if existing_doc is not None:
        if reset:
            logger.info("Reset: deleting existing chunks for %s", doc.doc_id)
            await session.execute(
                delete(Chunk).where(Chunk.document_id == doc.doc_id)
            )
            await session.execute(
                delete(Document).where(Document.id == doc.doc_id)
            )
            await session.flush()
            existing_doc = None
        else:
            logger.debug("Document %s already seeded; skipping", doc.doc_id)
            return 0

    # Insert document.
    db_doc = Document(
        id=doc.doc_id,
        organization_id=org_id,
        title=doc.title,
        source_type=doc.source_type,
        status="ready",
        tags=doc.tags,
        chunk_count=len(doc.chunks),
        s3_key=None,  # no raw file stored for seed content
    )
    session.add(db_doc)
    await session.flush()

    # Insert chunks with embeddings.
    for seed_chunk in doc.chunks:
        embedding: list[float] | None
        if fake_embeddings:
            embedding = _fake_embedding(seed_chunk.text)
        else:
            # Real embeddings require the TFY adapter; deferred to caller.
            embedding = None

        chunk = Chunk(
            id=new_id("chk"),
            document_id=doc.doc_id,
            organization_id=org_id,
            ordinal=seed_chunk.ordinal,
            text=seed_chunk.text,
            embedding=embedding,
            moss_ref=None,
            metadata_=seed_chunk.metadata or None,
        )
        session.add(chunk)

    await session.flush()
    logger.info(
        "Seeded document %s (%s) with %d chunks%s",
        doc.doc_id,
        doc.title,
        len(doc.chunks),
        " [fake-embeddings]" if fake_embeddings else "",
    )
    return len(doc.chunks)


async def _seed_customer(session: Any, org_id: str) -> str:
    """Upsert Sarah Chen / Acme Corp. Returns the customer id."""
    result = await session.execute(
        select(Customer).where(Customer.id == DEMO_CUSTOMER_ID)
    )
    if result.scalar_one_or_none() is not None:
        logger.debug("Customer %s already exists; skipping", DEMO_CUSTOMER_ID)
        return DEMO_CUSTOMER_ID

    customer = Customer(
        id=DEMO_CUSTOMER_ID,
        organization_id=org_id,
        name="Sarah Chen",
        company="Acme Corp",
        email="sarah.chen@acme.corp",
    )
    session.add(customer)
    await session.flush()
    logger.info("Seeded customer Sarah Chen / Acme Corp (%s)", DEMO_CUSTOMER_ID)
    return DEMO_CUSTOMER_ID


async def _seed_customer_memories(
    session: Any,
    customer_id: str,
    org_id: str,
    fake_embeddings: bool,
) -> None:
    """Seed Sarah Chen's interaction history (CRM ticket memory)."""
    from relay.db.models import Memory

    memories = [
        {
            "kind": "fact",
            "text": (
                "Customer Sarah Chen (Acme Corp, Growth tier) had a CRM export sync "
                "issue resolved on Mar 12 2026 via OAuth re-authentication. "
                "Ticket #1023."
            ),
        },
        {
            "kind": "fact",
            "text": (
                "Acme Corp is on the Growth Plan. Sarah Chen is the primary contact. "
                "Previous ticket: Onboarding setup (resolved Feb 2 2026)."
            ),
        },
    ]

    for mem_data in memories:
        # Idempotent: skip if an identical text entry already exists.
        from sqlalchemy import and_

        existing = await session.execute(
            select(Memory).where(
                and_(
                    Memory.customer_id == customer_id,
                    Memory.text == mem_data["text"],
                )
            )
        )
        if existing.scalar_one_or_none() is not None:
            logger.debug("Memory already exists for customer %s; skipping", customer_id)
            continue

        embedding: list[float] | None = (
            _fake_embedding(mem_data["text"]) if fake_embeddings else None
        )

        mem = Memory(
            id=new_id("mem"),
            customer_id=customer_id,
            organization_id=org_id,
            kind=mem_data["kind"],
            text=mem_data["text"],
            embedding=embedding,
        )
        session.add(mem)

    await session.flush()
    logger.info("Seeded memories for Sarah Chen")


async def _seed_intake_lead(session: Any, org_id: str) -> None:
    """Upsert the demo intake lead (Jordan Mraz / Brightwave Inc.)."""
    from relay.db.models import Lead

    # Ensure the anchor intake session exists (Lead requires a session_id FK).
    ses_result = await session.execute(
        select(Session).where(Session.id == DEMO_INTAKE_SESSION_ID)
    )
    if ses_result.scalar_one_or_none() is None:
        demo_session = Session(
            id=DEMO_INTAKE_SESSION_ID,
            organization_id=org_id,
            mode="intake",
            status="ended",
            ended_at=datetime(2026, 6, 5, 11, 33, 0, tzinfo=timezone.utc),
        )
        session.add(demo_session)
        await session.flush()
        logger.info("Seeded demo intake session %s", DEMO_INTAKE_SESSION_ID)

    # Check for existing lead on this session.
    lead_result = await session.execute(
        select(Lead).where(Lead.session_id == DEMO_INTAKE_SESSION_ID)
    )
    if lead_result.scalar_one_or_none() is not None:
        logger.debug("Lead for session %s already exists; skipping", DEMO_INTAKE_SESSION_ID)
        return

    lead = Lead(
        id=new_id("lead"),
        session_id=DEMO_INTAKE_SESSION_ID,
        organization_id=org_id,
        name="Jordan Mraz",
        company="Brightwave Inc.",
        email="vp.eng@brightwave.io",
        qualifiers={
            "budget": "$40k-$60k/year",
            "timeline": "this quarter",
            "need": "reduce onboarding latency; reps spend too much time looking up technical specs",
            "decision_maker": "VP of Engineering",
        },
        score=82,
        status="hot",
        routed_to=None,
    )
    session.add(lead)
    await session.flush()
    logger.info("Seeded intake lead Jordan Mraz / Brightwave Inc.")


async def _seed_audit_log(session: Any, org_id: str, action: str, target_id: str) -> None:
    """Write a seed audit log entry."""
    entry = AuditLog(
        id=new_id("aud"),
        organization_id=org_id,
        actor_id=None,
        action=action,
        target_type="document",
        target_id=target_id,
        metadata_={"seed": True},
    )
    session.add(entry)


# ---------------------------------------------------------------------------
# Real embeddings path (requires TFY creds)
# ---------------------------------------------------------------------------


async def _embed_via_tfy(texts: list[str]) -> list[list[float]]:
    """Embed texts using the TrueFoundry adapter. Requires TFY creds in env."""
    from relay.adapters.embeddings_tfy import TfyEmbeddings  # type: ignore[import]

    embedder = TfyEmbeddings()
    return await embedder.embed(texts)


async def _apply_real_embeddings(
    session: Any, doc_id: str, org_id: str
) -> None:
    """Fetch all un-embedded chunks for a document and fill in real vectors."""
    result = await session.execute(
        select(Chunk).where(
            Chunk.document_id == doc_id,
            Chunk.organization_id == org_id,
            Chunk.embedding.is_(None),
        )
    )
    chunks: list[Chunk] = list(result.scalars().all())
    if not chunks:
        return

    texts = [c.text for c in chunks]
    vectors = await _embed_via_tfy(texts)

    for chunk, vector in zip(chunks, vectors):
        chunk.embedding = vector  # type: ignore[assignment]

    await session.flush()
    logger.info("Applied real embeddings to %d chunks of %s", len(chunks), doc_id)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def seed(
    org_id: str,
    fake_embeddings: bool = True,
    reset: bool = False,
) -> None:
    """Seed the Northwind demo data into the database.

    Args:
        org_id:          UUID string of the target organisation.
        fake_embeddings: Use deterministic local hash embeddings (no external creds).
        reset:           Drop and re-insert all seeded documents before inserting.
    """
    logger.info(
        "Starting Northwind seed (org=%s, fake_embeddings=%s, reset=%s)",
        org_id,
        fake_embeddings,
        reset,
    )

    async with privileged_session() as session:
        # 1. Ensure demo org exists.
        await _ensure_demo_org(session, org_id)

        # 2. Seed documents + chunks.
        total_chunks = 0
        for doc_def in SEED_DOCUMENTS:
            n = await _seed_document(
                session, doc_def, org_id, fake_embeddings=fake_embeddings, reset=reset
            )
            total_chunks += n

            # Optionally apply real embeddings for chunks inserted without vectors.
            if not fake_embeddings and n > 0:
                await _apply_real_embeddings(session, doc_def.doc_id, org_id)

            if n > 0:
                await _seed_audit_log(session, org_id, "seed.document", doc_def.doc_id)

        # 3. Seed Sarah Chen customer + memories.
        customer_id = await _seed_customer(session, org_id)
        await _seed_customer_memories(
            session, customer_id, org_id, fake_embeddings=fake_embeddings
        )

        # 4. Seed intake lead (Jordan Mraz).
        await _seed_intake_lead(session, org_id)

    logger.info(
        "Northwind seed complete. %d document(s), %d total chunks seeded.",
        len(SEED_DOCUMENTS),
        total_chunks,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m relay.seed.northwind",
        description=(
            "Seed the Northwind demo dataset into the Relay database. "
            "All operations are idempotent."
        ),
    )
    parser.add_argument(
        "--fake-embeddings",
        action="store_true",
        default=False,
        help=(
            "Use deterministic local hash embeddings (dim 1024) instead of calling "
            "the TrueFoundry embeddings service. Allows seeding without external creds."
        ),
    )
    parser.add_argument(
        "--org-id",
        default=None,
        metavar="UUID",
        help=(
            "Override the target organisation UUID. "
            "Defaults to settings.default_org_id "
            f"(currently: {settings.default_org_id})."
        ),
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        default=False,
        help="Delete and re-insert all seeded documents before inserting (for a clean re-seed).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Enable DEBUG-level logging.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    org_id: str = args.org_id or settings.default_org_id

    # Validate UUID format.
    try:
        uuid.UUID(org_id)
    except ValueError:
        print(f"ERROR: --org-id must be a valid UUID; got: {org_id!r}", file=sys.stderr)
        sys.exit(1)

    asyncio.run(
        seed(
            org_id=org_id,
            fake_embeddings=args.fake_embeddings,
            reset=args.reset,
        )
    )


if __name__ == "__main__":
    main()

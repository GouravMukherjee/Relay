"""Seed the Moss index with the Northwind demo knowledge base.

Moss has built-in embeddings, so this pushes the chunk *text* straight into the
configured Moss index (``settings.moss_index_name``), tagged with the demo
``organization_id`` for tenant-scoped retrieval. No TFY embeddings, no Postgres
writes — this is the retrieval index the live query path reads.

Run:
    python -m relay.seed.moss_index            # seed the default demo org
    python -m relay.seed.moss_index --org-id <uuid>
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from relay.config import settings
from relay.interfaces.retrieval import RetrievedChunk
from relay.seed.northwind import SEED_DOCUMENTS

logger = logging.getLogger("relay.seed.moss_index")


def _build_chunks() -> list[RetrievedChunk]:
    chunks: list[RetrievedChunk] = []
    for doc in SEED_DOCUMENTS:
        for ch in doc.chunks:
            cid = f"chk_{doc.doc_id}_{ch.ordinal}"
            chunks.append(
                RetrievedChunk(
                    chunk_id=cid,
                    document_id=doc.doc_id,
                    title=doc.title,
                    text=ch.text,
                    snippet=ch.text[:200],
                    score=0.0,
                    moss_ref=cid,
                )
            )
    return chunks


async def _write_postgres_rows(org_id: str) -> None:
    """Upsert documents + chunks (NULL embedding) into Postgres.

    Moss is the retrieval index, but Postgres remains the system of record: the KB
    screen reads ``documents`` and ``card_sources.chunk_id`` FK-references ``chunks``.
    Embeddings are NULL (Moss embeds server-side); the pgvector fallback is inactive.
    Idempotent: replaces chunks for each seeded document. Runs via the privileged
    (RLS-bypassing) pooler role with explicit organization_id.
    """
    from sqlalchemy import delete as sa_delete
    from relay.db.base import privileged_session
    from relay.db.models import Chunk, Document

    async with privileged_session() as db:
        for doc in SEED_DOCUMENTS:
            existing = await db.get(Document, doc.doc_id)
            if existing is None:
                db.add(
                    Document(
                        id=doc.doc_id,
                        organization_id=org_id,
                        title=doc.title,
                        source_type=doc.source_type,
                        status="ready",
                        tags=list(doc.tags),
                        chunk_count=len(doc.chunks),
                        s3_key=None,
                    )
                )
            else:
                existing.organization_id = org_id
                existing.title = doc.title
                existing.status = "ready"
                existing.chunk_count = len(doc.chunks)
            # Replace chunks for idempotency.
            await db.execute(sa_delete(Chunk).where(Chunk.document_id == doc.doc_id))
            for ch in doc.chunks:
                db.add(
                    Chunk(
                        id=f"chk_{doc.doc_id}_{ch.ordinal}",
                        document_id=doc.doc_id,
                        organization_id=org_id,
                        ordinal=ch.ordinal,
                        text=ch.text,
                        embedding=None,  # Moss embeds server-side; pgvector fallback inactive
                        moss_ref=f"chk_{doc.doc_id}_{ch.ordinal}",
                    )
                )
        await db.commit()


async def seed_moss(org_id: str) -> int:
    """Index all demo chunks into Moss + write the SoR rows. Returns the chunk count."""
    from relay.adapters.moss import MossRetrieval

    chunks = _build_chunks()
    await _write_postgres_rows(org_id)
    moss = MossRetrieval()
    await moss.index_with_org(chunks, org_id=org_id)
    return len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the Moss index with demo data.")
    parser.add_argument("--org-id", default=settings.default_org_id, help="Target organization_id.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    n = asyncio.run(seed_moss(args.org_id))
    print(
        f"Seeded {n} chunks from {len(SEED_DOCUMENTS)} documents into Moss index "
        f"'{settings.moss_index_name}' for org {args.org_id}."
    )


if __name__ == "__main__":
    main()

"""Standalone proof that the org_isolation RLS policies actually enforce
tenant isolation against the real migration. Run against a migrated Postgres:

    DATABASE_URL=postgresql://relay:relay@localhost:5433/relay \
        python tests/_rls_live_proof.py

Connects as the NON-superuser, RLS-subject `relay_app` role (the role the
request path uses in production) and verifies read + write isolation. Not part
of the pytest suite; a one-shot invariant #3 check.
"""

import asyncio
import json
import os
import uuid

import asyncpg

SUPER_DSN = os.environ["SUPER_DSN"]  # superuser (relay) — seeds, bypasses RLS
APP_DSN = os.environ["APP_DSN"]      # relay_app — RLS-subject


def _doc(org_id: str, title: str) -> str:
    return uuid.uuid4().hex[:24]


async def main() -> None:
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    doc_a = f"doc_{uuid.uuid4().hex[:20]}"
    doc_b = f"doc_{uuid.uuid4().hex[:20]}"

    # 1) Seed both orgs' documents as the superuser (RLS bypassed for setup).
    su = await asyncpg.connect(SUPER_DSN)
    await su.execute("DELETE FROM documents")
    for org in (org_a, org_b):
        await su.execute(
            "INSERT INTO organizations (id, name, created_at) VALUES ($1, $2, now())"
            " ON CONFLICT (id) DO NOTHING",
            uuid.UUID(org), f"Org {org[:8]}",
        )
    for did, org, title in [(doc_a, org_a, "A-only.pdf"), (doc_b, org_b, "B-only.pdf")]:
        await su.execute(
            "INSERT INTO documents (id, organization_id, title, source_type, status, chunk_count, created_at)"
            " VALUES ($1, $2, $3, 'pdf', 'ready', 1, now())",
            did, uuid.UUID(org), title,
        )
    await su.close()

    failures = []

    # 2) As relay_app with org A's claims -> only org A's doc visible.
    app = await asyncpg.connect(APP_DSN)
    async with app.transaction():
        await app.execute(
            "SELECT set_config('request.jwt.claims', $1, true)",
            json.dumps({"org_id": org_a, "role": "owner"}),
        )
        rows = await app.fetch("SELECT id FROM documents")
        ids = {r["id"] for r in rows}
    if doc_a not in ids:
        failures.append("org A could NOT see its own doc")
    if doc_b in ids:
        failures.append("RLS LEAK: org A saw org B's doc")
    print(f"[org A claims] visible docs = {sorted(ids)}")

    # 3) As relay_app with org B's claims -> only org B's doc visible.
    async with app.transaction():
        await app.execute(
            "SELECT set_config('request.jwt.claims', $1, true)",
            json.dumps({"org_id": org_b, "role": "owner"}),
        )
        rows = await app.fetch("SELECT id FROM documents")
        ids = {r["id"] for r in rows}
    if doc_b not in ids:
        failures.append("org B could NOT see its own doc")
    if doc_a in ids:
        failures.append("RLS LEAK: org B saw org A's doc")
    print(f"[org B claims] visible docs = {sorted(ids)}")

    # 4) WITH CHECK write isolation: org A may not insert a row for org B.
    write_blocked = False
    try:
        async with app.transaction():
            await app.execute(
                "SELECT set_config('request.jwt.claims', $1, true)",
                json.dumps({"org_id": org_a, "role": "owner"}),
            )
            await app.execute(
                "INSERT INTO documents (id, organization_id, title, source_type, status, chunk_count, created_at)"
                " VALUES ($1, $2, 'sneaky', 'pdf', 'ready', 1, now())",
                f"doc_{uuid.uuid4().hex[:20]}", uuid.UUID(org_b),
            )
    except asyncpg.exceptions.CheckViolationError:
        write_blocked = True
    except Exception as exc:  # row-security WITH CHECK surfaces here too
        write_blocked = "row-level security" in str(exc).lower() or "policy" in str(exc).lower()
    if not write_blocked:
        failures.append("WITH CHECK LEAK: org A inserted a row for org B")
    print(f"[write isolation] org A -> org B insert blocked = {write_blocked}")

    await app.close()

    if failures:
        print("\nRLS PROOF FAILED:")
        for f in failures:
            print("  - " + f)
        raise SystemExit(1)
    print("\nRLS PROOF PASSED: read + write tenant isolation enforced by org_isolation policies.")


if __name__ == "__main__":
    asyncio.run(main())

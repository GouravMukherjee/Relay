# CLAUDE.md — Relay

> Anchor file for Claude Code. Keep it short; it loads every turn. It points to the
> deep docs in `./docs` rather than duplicating them. Read the docs before building.

## What Relay is

An ambient AI co-pilot that listens to a live conversation and surfaces a **grounded,
cited answer from the company's own documents in under 500 ms**. One engine, three modes:
**Live** (co-pilot), **Desk** (support), **Intake** (lead gen). Multi-tenant SaaS.

**Prime directive:** every answer is retrieved and cited from the tenant's indexed docs.
Never invent answers. Grounded or silent.

## Docs index (source of truth)

- `docs/PRD.md` — what/why, the three modes, success criteria
- `docs/TECHNICAL_DESIGN.md` — architecture, latency budget, components
- `docs/API_SPEC.md` — **FROZEN CONTRACT.** REST + WebSocket shapes. Obey exactly.
- `docs/DATA_MODEL.md` — **FROZEN CONTRACT.** Postgres schema. Obey exactly.
- `docs/SCOPE.md` — in / stretch / out; cut-lines
- `docs/DEMO_SCRIPT.md` — the demo flow + Northwind dataset (build toward this)
- `docs/ADRs.md` — why key decisions were made
- `docs/TEST_PLAN.md` — demo-critical, latency, grounding, tenant-isolation tests
- `docs/BUILD_SPEC_AND_MASTER_PROMPT.md` — setup, sponsors, auth, deploy

Ask before deviating from a FROZEN CONTRACT.

## Tech stack (do not substitute without asking)

- **Frontend:** Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui → Vercel
- **Backend:** Python 3.11 + FastAPI (REST + WebSocket), fully async
- **Real-time:** LiveKit Agents worker · **STT:** LiveKit Inference (e.g. AssemblyAI
  `universal-streaming`, billed on the LiveKit key) · **TTS (optional):** Minimax
- **LLM:** Claude (primary) via the **TrueFoundry AI Gateway**; Qwen + Minimax as
  alternates behind the same gateway. One `LLMClient`, model selectable by config.
- **Retrieval:** Moss (<10 ms) primary, pgvector fallback
- **Parsing:** Unsiloed · **Embeddings:** behind an interface
- **Data/Auth/Storage:** Supabase Postgres + pgvector + Supabase Auth + RLS · AWS S3 (raw files) · Redis (arq queue + cache)

## Repo structure

```
CLAUDE.md  .claude/agents/  docs/
backend/   gateway/ agent/ retrieval/ ingestion/ orchestrator/ memory/ auth/ db/
frontend/  (Next.js app, components, lib)
data/demo/ (Northwind dataset)
```

## Commands

```bash
cp .env.example .env          # fill every key (see BUILD_SPEC table)
docker compose up -d postgres redis
make migrate                  # schema + RLS policies
make seed-demo                # ingest data/demo/* via the ingestion path
make run-gateway              # FastAPI :8000
make run-agent                # LiveKit agent worker
make run-worker               # arq ingestion worker
cd frontend && npm run dev    # :5173 / :3000
make test                     # pytest — TEST_PLAN critical path
```

## Architecture invariants (do not violate)

1. **Live path never touches raw files.** During a call, query the pre-built Moss index
   only. Ingestion is async and offline. Scanning files mid-call breaks the <500 ms budget.
2. **Grounding guard.** The orchestrator answers ONLY from retrieved chunks and must cite
   them. If no chunk is relevant, return "no card." Never hallucinate.
3. **Tenant isolation is enforced at the DB.** Every tenant table has `organization_id`
   with Postgres RLS. Never trust a client-supplied `org_id` — take it from the verified
   JWT. App-layer checks are not enough; RLS is the backstop.
4. **Sponsor services live behind interfaces.** `RetrievalService` (Moss + pgvector
   fallback), `DocumentParser` (Unsiloed), `Embeddings`, `LLMClient` (TFY gateway). Each
   has a mock impl for tests. Never hardcode a vendor call in business logic.
5. **LiveKit tokens are minted server-side**, room-scoped, short TTL. The LiveKit secret
   never reaches the client.
6. **Ingestion is idempotent** by `document_id`; re-ingest replaces, never duplicates.

## Conventions

- **Provide full file contents, never partial snippets.**
- Maintain the existing design system (light, indigo `#4F46E5` brand, emerald `#10B981`
  for verified/success only, hairline `#E4E4E7`, shadcn/ui). Don't redesign from scratch.
- Python: type hints everywhere; Pydantic models for all I/O; async-first; structured
  logging with request id + latency. TypeScript: strict mode.
- Endpoints match `API_SPEC.md` exactly (paths, event names, object shapes).
- Secrets via env only; never commit `.env`; never log secrets or full transcripts.
- A `/health` endpoint; meaningful errors using the `API_SPEC` error codes.

## Security (see BUILD_SPEC §5)

RLS on all tenant tables · JWT verified against Supabase JWKS · Pydantic input validation
· presigned S3 uploads · CORS locked to the frontend origin · per-org rate limiting ·
`audit_log` on doc upload/delete, connector connect, session start, role change.

## How to work here

- Read `docs/` first. Treat `API_SPEC.md` + `DATA_MODEL.md` as frozen.
- Build in the order in `BUILD_SPEC_AND_MASTER_PROMPT.md` §7; after each step, summarize
  what changed and what to run, then continue.
- When unsure about a sponsor API shape, implement against the interface + mock and leave a
  clearly marked `# TODO: confirm <vendor> API` rather than guessing endpoints.
- Definition of done for a feature: matches the contract, has a test from `TEST_PLAN.md`,
  enforces tenant isolation, and never breaks the grounding guard or the latency budget.

## Gotchas

- Cold vector lookups are 97–307 ms; the voice budget is ~200 ms. Protect it: stream card
  tokens, pre-warm the LLM connection, cache demo queries, keep Moss the primary path.
- `Explore` and `Plan` subagents skip this file — give them explicit context.
- Don't add dependencies without flagging them.
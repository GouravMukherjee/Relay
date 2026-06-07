# Relay — Build Spec & Master Prompt

**Status:** Canonical · **Owner:** Systems · **Last updated:** June 2026

> This is the build-setup "bible" referenced by `CLAUDE.md`. It covers sponsor
> services, architecture, auth, env vars, security, and the deploy path. Read it
> before writing code that touches infrastructure or credentials.

---

## Recent corrections baked in

> **1. STT now uses LiveKit Inference — there is no Deepgram.**
> The LiveKit Agent worker streams audio to a model served via LiveKit's own
> inference tier. `DEEPGRAM_API_KEY` does not exist; do not add it.
>
> **2. `FRONTEND_ORIGIN` is a comma-separated, slash-free list.**
> Example: `https://relay.vercel.app,http://localhost:5173`. The backend CORS
> middleware splits on commas, strips trailing slashes, and builds the allowed
> origins list at startup. Never include a trailing `/`; never use `*` with
> credentials.

---

## 1. Sponsor services — what to claim & set up

| Sponsor / Service | Role in Relay | Env var(s) |
|---|---|---|
| **Moss** | Primary retrieval index (<10 ms). All live-path queries hit Moss first. | `MOSS_API_KEY`, `MOSS_BASE_URL` |
| **LiveKit Cloud** | Audio transport (rooms), LiveKit Agents framework, **STT via LiveKit Inference** (streaming speech-to-text billed against LiveKit credits — no separate STT vendor). | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_STT_MODEL` *(optional, default `assemblyai/universal-streaming`)* |
| **Unsiloed** | Document parsing — PDFs, docx, tables, layout-aware. Called during async ingestion only. | `UNSILOED_API_KEY` |
| **TrueFoundry (TFY)** | AI Gateway (routes LLM calls to Claude / Qwen / Minimax behind one interface) **and** cloud deploy target for all three backend services. | `TFY_API_KEY`, `TFY_GATEWAY_URL` |
| **Anthropic** | Claude (primary LLM for synthesis), accessed via TFY Gateway. | `ANTHROPIC_API_KEY` |
| **Qwen** | Alternate LLM behind TFY Gateway. | `QWEN_API_KEY` |
| **Minimax** | Alternate LLM + optional TTS behind TFY Gateway. | `MINIMAX_API_KEY` |
| **Supabase** | Postgres + pgvector (system-of-record DB), Supabase Auth (JWTs verified via JWKS), Row-Level Security enforcement. | `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`, `SUPABASE_JWT_SECRET` *(optional, local/test HS256)*, `SUPABASE_JWT_ISSUER` *(optional override)* |
| **AWS S3** | Raw file storage for uploaded documents. Accessed via presigned URLs only. | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET` |
| **Vercel** | Frontend hosting (React/Vite SPA). Import the repo, set root to `frontend/`, preset Vite. | Configured via Vercel UI — see §6 |
| **Redis** | `arq` task queue (ingestion jobs) + result cache. | `REDIS_URL` |
| **Slack** | Optional lead-routing webhook (Intake mode). | `SLACK_WEBHOOK_URL` *(optional)* |

### How the LLM layer works

There is exactly **one `LLMClient`** in the codebase. It calls the TrueFoundry AI
Gateway (OpenAI-compatible endpoint at `TFY_GATEWAY_URL`). The model is selected by the
`LLM_MODEL` env var (`claude` | `qwen` | `minimax`). The individual provider API keys
(`ANTHROPIC_API_KEY`, etc.) are forwarded by the gateway — they live on the backend only.

### How STT works

The LiveKit Agents worker calls LiveKit's Inference tier for streaming STT. The model
identifier (e.g. `assemblyai/universal-streaming`) is set in `LIVEKIT_STT_MODEL`. STT
costs are billed against your LiveKit project credits. **There is no Deepgram
integration and no `DEEPGRAM_API_KEY`.**

---

## 2. Production architecture

### Services (one Docker image, three start commands)

The backend is a **monolith** — retrieval and orchestrator run in-process inside the
gateway. There is no separate retrieval microservice.

| TFY Service | Start command | Ingress | Purpose |
|---|---|---|---|
| `relay-gateway` | `uvicorn relay.gateway.app:app --port 8000` | Public (HTTP + WS) | REST `/api/v1/*`, WebSocket `/ws/sessions/*` |
| `relay-agent` | `python -m relay.agent.worker start` | None (outbound only) | LiveKit room worker: STT, trigger detection, card dispatch |
| `relay-ingest` | `arq relay.ingestion.worker.WorkerSettings` | None | Async document ingestion queue |
| `relay-migrate` | `alembic -c alembic.ini upgrade head` | Job (one-off) | Schema + RLS migrations |

### Architecture diagram

```
  ┌─────────────────────────────────┐
  │  React 18 + Vite (Vercel)       │
  │  dashboard / card UI            │
  └────────────┬────────────────────┘
               │  REST /api/v1/*
               │  WebSocket /ws/sessions/*
               ▼
  ┌─────────────────────────────────┐         ┌─────────────────────────┐
  │  FastAPI Gateway (relay-gateway)│         │  LiveKit Agent Worker   │
  │  ├─ REST endpoints              │◄────────│  (relay-agent)          │
  │  ├─ WebSocket hub               │  events │  ├─ joins LiveKit room   │
  │  ├─ Orchestrator (in-process)   │         │  ├─ STT via LK Inference │
  │  │    └─ LLMClient → TFY GW     │         │  ├─ Trigger detector     │
  │  └─ RetrievalService (in-proc)  │         │  └─ emits query events   │
  │       ├─ Moss (<10 ms)          │         └─────────────────────────┘
  │       └─ pgvector (fallback)    │
  └────────────┬────────────────────┘
               │
               ▼
  ┌──────────────────────────────────────────────────────┐
  │  Supabase Postgres + pgvector                        │
  │  organizations · users · documents · chunks          │
  │  sessions · utterances · cards · card_sources        │
  │  customers · memories · leads · audit_log            │
  └──────────────────────────────────────────────────────┘

  ┌───────────────────────┐    ┌────────────────────┐
  │  arq Ingestion Worker │    │  Redis             │
  │  (relay-ingest)       │◄──►│  queue + cache     │
  │  upload → Unsiloed    │    └────────────────────┘
  │  → chunk → embed      │
  │  → Moss + pgvector    │    ┌────────────────────┐
  └───────────────────────┘    │  AWS S3            │
                               │  raw file storage  │
                               └────────────────────┘
```

### Latency budget (Relay Live mode)

| Stage | Target |
|---|---|
| STT partial → final (LiveKit Inference) | ~100–200 ms |
| Trigger decision | <5 ms |
| Moss retrieval | <10 ms |
| Claude synthesis (short card, streamed) | ~150–300 ms |
| WebSocket push + render | <30 ms |
| **Perceived (trigger → first card token)** | **<500 ms** |

Mitigations: stream card tokens token-by-token (`card.update` WS events); pre-warm the
TFY Gateway connection on startup; cache embeddings of the rolling query window; keep
Moss as the primary retrieval path (pgvector fallback is demo-safe but slower).

---

## 3. Auth & multi-tenancy

### Flow

1. **Sign-in:** Supabase Auth issues a signed JWT (RS256 in production, HS256 optional
   for local dev via `SUPABASE_JWT_SECRET`).
2. **Verification:** FastAPI's `relay.auth.deps` middleware fetches the Supabase JWKS
   at `{SUPABASE_URL}/.well-known/jwks.json` and verifies every inbound token signature
   and expiry. The override `SUPABASE_JWT_ISSUER` lets you point at a different issuer
   in test environments.
3. **Bootstrap:** On the first sign-in for a new user, `_bootstrap_principal` creates
   the `organizations` row and `users` row (role=`owner`) in a single transaction. All
   subsequent requests attach the `org_id` from the verified JWT claims — never from a
   client-supplied parameter.
4. **RLS:** Every tenant table (`documents`, `chunks`, `sessions`, `utterances`, `cards`,
   `card_sources`, `customers`, `memories`, `leads`) has a Postgres RLS policy named
   `org_isolation` that filters by `organization_id` using the session-local variable set
   from the verified JWT. Application-layer `org_id` checks are a convenience; RLS is the
   backstop.
5. **LiveKit tokens:** Minted server-side (`POST /sessions` response includes
   `livekit_token`), room-scoped, short TTL. The `LIVEKIT_API_SECRET` never reaches the
   client.

### Roles

| Role | Permissions |
|---|---|
| `owner` | Full org access; can invite members, manage connectors |
| `member` | Read/write sessions, documents, leads within the org |
| `viewer` | Read-only within the org |

Role is stored in `users.role` and encoded in the JWT custom claims.

---

## 4. Env-var matrix

### Frontend (Vite — `VITE_*` prefix required)

> These are injected at build time by Vercel. Set them in the Vercel project
> "Environment Variables" UI for Production and Preview.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `VITE_DEMO_MODE` | No | `"true"` | `"false"` → connects to real backend; `"true"` → in-browser mock, no network |
| `VITE_BACKEND_URL` | Yes (functional mode) | `http://localhost:8000` | Backend origin, e.g. `https://relay-gateway.your-tfy-domain`. Bare `host:port` is auto-prefixed with `http://` by `config.ts`. |
| `VITE_LIVEKIT_URL` | Yes (Live mode) | — | LiveKit project URL, e.g. `wss://your-project.livekit.cloud` |
| `VITE_SUPABASE_URL` | Yes | — | `https://<project-ref>.supabase.co` |
| `VITE_SUPABASE_ANON_KEY` | Yes | — | Public anon key (safe to expose; RLS enforces isolation) |

> Do **not** use `NEXT_PUBLIC_*` — the frontend is React 18 + Vite, not Next.js.
> `VITE_API_BASE` and `VITE_WS_BASE` can be set to route through the Vite dev proxy
> (CORS-free local dev); leave them unset in production.

### Backend (Python — no prefix)

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | Yes | Pooled Supabase DSN (port 6543): `postgresql+asyncpg://...`. Use direct DSN (5432) only for the migrate job. |
| `REDIS_URL` | Yes | `redis://localhost:6379/0` or Upstash/ElastiCache URL |
| `SUPABASE_URL` | Yes | `https://<project-ref>.supabase.co` |
| `SUPABASE_ANON_KEY` | Yes | Public anon key |
| `SUPABASE_SERVICE_KEY` | Yes | Service-role key (bypasses RLS for admin ops only) |
| `SUPABASE_JWT_SECRET` | No | HS256 secret for local/test token verification |
| `SUPABASE_JWT_ISSUER` | No | Override JWKS issuer URL (defaults to `SUPABASE_URL`) |
| `MOSS_API_KEY` | Yes | Moss retrieval service key |
| `MOSS_BASE_URL` | Yes | `https://api.moss.ai` (or your Moss endpoint) |
| `TFY_API_KEY` | Yes | TrueFoundry API key (deploy + gateway auth) |
| `TFY_GATEWAY_URL` | Yes | `https://llm-gateway.truefoundry.com/api/inference/openai` |
| `ANTHROPIC_API_KEY` | Yes | Forwarded by TFY Gateway to Anthropic |
| `QWEN_API_KEY` | Yes | Forwarded by TFY Gateway to Qwen |
| `MINIMAX_API_KEY` | Yes | Forwarded by TFY Gateway to Minimax |
| `LLM_MODEL` | No | `claude` (default) \| `qwen` \| `minimax` |
| `UNSILOED_API_KEY` | Yes | Unsiloed document parser key |
| `LIVEKIT_URL` | Yes | `wss://<your-project>.livekit.cloud` |
| `LIVEKIT_API_KEY` | Yes | LiveKit API key (token minting + agent auth) |
| `LIVEKIT_API_SECRET` | Yes | LiveKit API secret — **never send to the client** |
| `LIVEKIT_STT_MODEL` | No | STT model served via LiveKit Inference. Default: `assemblyai/universal-streaming` |
| `AWS_ACCESS_KEY_ID` | Yes | IAM credentials for S3 |
| `AWS_SECRET_ACCESS_KEY` | Yes | IAM credentials for S3 |
| `AWS_REGION` | Yes | e.g. `us-east-1` |
| `S3_BUCKET` | Yes | e.g. `relay-documents` |
| `FRONTEND_ORIGIN` | Yes | Comma-separated list of allowed origins, **no trailing slash**. Example: `https://relay.vercel.app,http://localhost:5173`. Parsed at startup to build the CORS allowed-origins list. |
| `DEFAULT_ORG_ID` | Yes | Demo org UUID: `00000000-0000-0000-0000-000000000001`. Used by seed scripts and local dev. |
| `APP_DB_ROLE` | Yes | Postgres role used by the app (must match RLS policy setup): `relay_app` |
| `EMBEDDING_DIM` | No | Default `1024`. Must match the embedding model. |
| `SLACK_WEBHOOK_URL` | No | Incoming webhook URL for Intake lead routing notifications |

> Provider and service API keys (`ANTHROPIC_API_KEY`, `QWEN_API_KEY`, `MINIMAX_API_KEY`,
> `MOSS_API_KEY`, `UNSILOED_API_KEY`, `LIVEKIT_API_SECRET`, `AWS_SECRET_ACCESS_KEY`,
> `SUPABASE_SERVICE_KEY`) live **only on the backend**. They must never be bundled into the
> frontend build or logged.

---

## 5. Security checklist

| Control | Implementation |
|---|---|
| **Tenant isolation** | Postgres RLS `org_isolation` policy on every tenant table. `org_id` taken from the verified JWT only. |
| **JWT verification** | RS256 via Supabase JWKS endpoint at startup + per-request. HS256 (`SUPABASE_JWT_SECRET`) available for local/test only. |
| **Input validation** | Pydantic models on all request bodies. FastAPI rejects malformed payloads before they reach business logic. |
| **Presigned S3 uploads** | Raw files go directly from client to S3 via a presigned PUT URL. The backend never proxies file bytes. |
| **CORS** | `FRONTEND_ORIGIN` is a comma-separated, no-trailing-slash list parsed at startup. Credentials mode requires explicit origin — `*` is never used. |
| **Per-org rate limiting** | Redis-backed sliding window per `org_id`. Protects the retrieval and LLM paths from runaway usage. |
| **Audit log** | `audit_log` table records: document upload/delete, connector connect, session start, role change. Written inside the same transaction as the action. |
| **Secrets hygiene** | All secrets via env only. `.env` is gitignored. Structured logs never emit secret values or full transcript text. |
| **LiveKit token scope** | Server-side only, room-scoped, short TTL. `LIVEKIT_API_SECRET` is a backend env var only. |
| **Slack webhook** | Optional; if unset, lead routing silently skips the notification step rather than erroring. |

---

## 6. Deploy (brief)

The step-by-step deployment runbook lives at `backend/deploy/README.md`. It covers:

- Provisioning Supabase (pgvector extension, email/OAuth auth, pooled vs direct DSN)
- AWS S3 bucket + CORS for the Vercel origin
- Redis (TFY add-on / Upstash / ElastiCache)
- LiveKit Cloud project
- Creating TrueFoundry secrets group `relay` with all backend keys
- Running the four deploy scripts (`deploy_migrate.py`, `deploy_gateway.py`,
  `deploy_agent.py`, `deploy_ingest.py`)
- Vercel frontend import (root = `frontend/`, preset = **Vite**, set the four
  `VITE_*` vars for Production and Preview)
- Seeding demo data: `python -m relay.seed.northwind`

Quick reference:

```bash
# 1. Local dev
cp backend/.env.example backend/.env    # fill every key
docker compose up -d postgres redis
make migrate                            # schema + RLS policies
make seed-demo                          # ingest data/demo/* (Northwind)
make run-gateway                        # FastAPI :8000
make run-agent                          # LiveKit agent worker
make run-worker                         # arq ingestion worker
cd frontend && npm run dev              # Vite :5173

# 2. Verify
make test                               # pytest — TEST_PLAN critical paths
docker run --rm relay python -c "import relay.gateway.app; print('ok')"

# 3. Production (see backend/deploy/README.md for full runbook)
pip install truefoundry
tfy login --host <your-truefoundry-host>
export WORKSPACE_FQN=<cluster>:<workspace>
export GATEWAY_HOST=relay-gateway.<your-tfy-domain>
export FRONTEND_ORIGIN=https://<your-app>.vercel.app
cd backend
python deploy/deploy_migrate.py
python deploy/deploy_gateway.py
python deploy/deploy_agent.py
python deploy/deploy_ingest.py
```

---

## Related docs

`docs/PRD.md` · `docs/TECHNICAL_DESIGN.md` · `docs/API_SPEC.md` *(frozen contract)* ·
`docs/DATA_MODEL.md` *(frozen contract)* · `docs/SCOPE.md` · `docs/ADRs.md` ·
`docs/TEST_PLAN.md` · `docs/DEMO_SCRIPT.md` · `backend/deploy/README.md`

# Relay — Deployment Guide

> Canonical reference for deploying Relay to production:
> TrueFoundry (backend), Vercel (frontend), Supabase, AWS S3, Redis, LiveKit.
>
> **Read `docs/TECHNICAL_DESIGN.md` and `docs/API_SPEC.md` first.**
> Treat `API_SPEC.md` and `DATA_MODEL.md` as frozen contracts — do not deviate.

---

## Table of contents

1. [Topology](#1-topology)
2. [Part A — Provision dependencies](#2-part-a--provision-dependencies)
3. [Part B — TrueFoundry secrets](#3-part-b--truefoundry-secrets)
4. [Part C — Deploy order](#4-part-c--deploy-order)
5. [Part D — Frontend (Vercel)](#5-part-d--frontend-vercel)
6. [Env-var matrix](#6-env-var-matrix)
7. [Verification checklist](#7-verification-checklist)
8. [Known gaps](#8-known-gaps)
9. [Related docs](#9-related-docs)

---

## 1. Topology

```
                        ┌──────────────────────────────────────┐
                        │          BROWSER (Vercel CDN)        │
                        │  React / Vite SPA  (VITE_* env vars) │
                        └────────────┬─────────────────────────┘
                                     │ HTTPS REST  /api/v1/*
                                     │ WSS         /ws/sessions/{id}
                                     ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│  TrueFoundry cluster                                                           │
│                                                                                │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │  relay-gateway  (public Service, port 8000)                             │  │
│  │  uvicorn relay.gateway.app:app                                          │  │
│  │  Monolith: REST routes + WebSocket hub + retrieval + orchestrator       │  │
│  │  Health probe: GET /health → {"status":"ok"}                            │  │
│  └──────────┬────────────────────────────────────────────────┬─────────────┘  │
│             │                                                │                │
│  ┌──────────▼──────────────┐          ┌─────────────────────▼──────────────┐  │
│  │  relay-agent            │          │  relay-ingest                      │  │
│  │  (no inbound port)      │          │  (no inbound port)                 │  │
│  │  python -m relay.agent  │          │  arq relay.ingestion.worker        │  │
│  │    .worker start        │          │    .WorkerSettings                 │  │
│  │  Joins LiveKit rooms,   │          │  Consumes arq/Redis queue;         │  │
│  │  streams audio via      │          │  S3 → Unsiloed → chunks →          │  │
│  │  LiveKit STT Inference, │          │  embeddings → Moss/pgvector        │  │
│  │  triggers orchestrator  │          └──────────────────┬─────────────────┘  │
│  └──────────┬──────────────┘                             │                    │
│             │                                            │                    │
│  ┌──────────▼──────────────┐                             │                    │
│  │  relay-migrate  (Job)   │                             │                    │
│  │  alembic upgrade head   │                             │                    │
│  │  Runs once; uses DIRECT │                             │                    │
│  │  DSN (port 5432)        │                             │                    │
│  └─────────────────────────┘                             │                    │
└────────────────────────────────────────────────────────────────────────────────┘
            │                          │
            ▼                          ▼
  ┌──────────────────┐      ┌────────────────────────────────────────┐
  │ LiveKit Cloud    │      │  Supabase                              │
  │ wss://<proj>     │      │  Postgres + pgvector  (pooled :6543)   │
  │   .livekit.cloud │      │  Auth (RS256 JWKS)                     │
  │ STT Inference    │      └──────────────┬─────────────────────────┘
  └──────────────────┘                     │
                                           ▼
                              ┌────────────────────────┐
                              │  Redis                 │
                              │  arq queue + cache     │
                              └────────────────────────┘
                                           │
                              ┌────────────▼────────────┐
                              │  AWS S3                 │
                              │  raw uploaded files     │
                              └─────────────────────────┘
```

**Monolith note:** `backend/relay` is a monolith — retrieval and orchestrator run
in-process inside `relay-gateway`. There is **no separate retrieval service** and
**no `RETRIEVAL_URL`** env var. The `docker/` skeleton in the repo is a stub and is
not deployed.

**STT note:** Speech-to-text is handled by **LiveKit Inference** (a model string such
as `assemblyai/universal-streaming`), routed by LiveKit using the existing
`LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` and billed on LiveKit credits. There is no
Deepgram dependency in the production design.

---

## 2. Part A — Provision dependencies

### 2.1 Supabase

1. Create a new Supabase project (region closest to your TrueFoundry cluster).
2. In the SQL editor run:
   ```sql
   create extension if not exists vector;
   ```
3. Under **Authentication → Providers** enable Email and (optionally) Google OAuth.
   Set the redirect URL to your Vercel domain, e.g. `https://relay-omega-five.vercel.app`.
4. Collect from **Project Settings → Database**:
   - **Pooled DSN** (Transaction mode, port **6543**) — used by `relay-gateway`,
     `relay-agent`, `relay-ingest` at runtime (async pool via `asyncpg`).
   - **Direct DSN** (port **5432**) — used by `relay-migrate` (Alembic DDL; pgBouncer
     does not support DDL in transaction mode).
5. Collect from **Project Settings → API**:
   - `SUPABASE_URL` — `https://<project-ref>.supabase.co`
   - `SUPABASE_ANON_KEY` — the anon/public JWT (safe for the frontend)
   - `SUPABASE_SERVICE_KEY` — the service-role key (server-side only, never expose)

> **JWT verification:** RS256 tokens are verified via the Supabase JWKS endpoint
> derived from `SUPABASE_URL`. No JWT secret is needed in production. A custom
> claims hook is optional — `relay.auth.deps._bootstrap_principal` creates the org
> and owner membership and back-fills `org_id` on first sign-in.

### 2.2 AWS S3

1. Create a private S3 bucket (e.g. `relay-documents-prod`).
2. Add a CORS configuration allowing `PUT` and `GET` from the Vercel origin:
   ```json
   [
     {
       "AllowedOrigins": ["https://<your-app>.vercel.app"],
       "AllowedMethods": ["GET", "PUT"],
       "AllowedHeaders": ["*"],
       "MaxAgeSeconds": 3600
     }
   ]
   ```
3. Create an IAM user / role with `s3:PutObject`, `s3:GetObject`,
   `s3:DeleteObject` on the bucket ARN. Collect `AWS_ACCESS_KEY_ID` and
   `AWS_SECRET_ACCESS_KEY`.

### 2.3 Redis

Any Redis instance reachable from the TrueFoundry cluster works:

| Option | Notes |
|--------|-------|
| TrueFoundry add-on | Easiest; same VPC as services |
| Upstash | Serverless; good for low traffic |
| AWS ElastiCache | Best for production scale |

Collect the connection URL in the form `redis://[:<password>@]<host>:<port>/<db>`.

### 2.4 LiveKit Cloud

1. Create a project at [cloud.livekit.io](https://cloud.livekit.io).
2. Collect:
   - `LIVEKIT_URL` — `wss://<your-project>.livekit.cloud`
   - `LIVEKIT_API_KEY` — from project settings
   - `LIVEKIT_API_SECRET` — from project settings (server-side only, never expose)
3. LiveKit Inference (STT) is billed on LiveKit credits; no separate STT account
   needed. The default STT model is `assemblyai/universal-streaming`; override with
   `LIVEKIT_STT_MODEL` if needed (see the env-var matrix).

### 2.5 Moss retrieval

Obtain `MOSS_API_KEY` and `MOSS_BASE_URL` from your Moss account. The gateway uses
Moss as the primary retrieval path (<10 ms); pgvector is the fallback.

### 2.6 TrueFoundry AI Gateway and LLM provider keys

| Key | Purpose |
|-----|---------|
| `TFY_API_KEY` | Authenticates calls to the TrueFoundry AI Gateway |
| `TFY_GATEWAY_URL` | Base URL, e.g. `https://llm-gateway.truefoundry.com/api/inference/openai` |
| `ANTHROPIC_API_KEY` | Claude (primary LLM) routed through the TFY gateway |
| `QWEN_API_KEY` | Qwen alternate model |
| `MINIMAX_API_KEY` | Minimax alternate model |

Default active model: `LLM_MODEL=claude`. Change to `qwen` or `minimax` as needed.

### 2.7 Unsiloed (document parsing)

Obtain `UNSILOED_API_KEY` from your Unsiloed account. Used by `relay-ingest` to parse
uploaded documents before chunking and embedding.

---

## 3. Part B — TrueFoundry secrets

Create a secrets **group** named `relay` in the TrueFoundry UI (Secrets → New group).
Add each secret individually; they are referenced in the deploy scripts as
`tfy-secret://relay/<key>`.

```
Group: relay
──────────────────────────────────────────────────────────────────────────────
Key                     Description
──────────────────────────────────────────────────────────────────────────────
database-url            POOLED Supabase DSN (port 6543)
                        postgresql+asyncpg://<user>:<pass>@<host>:6543/<db>
                        Used by relay-gateway, relay-agent, relay-ingest.

database-url-direct     DIRECT Supabase DSN (port 5432)
                        postgresql+asyncpg://<user>:<pass>@<host>:5432/<db>
                        Used ONLY by relay-migrate (Alembic DDL).

redis-url               redis://[:<pass>@]<host>:<port>/0

supabase-url            https://<project-ref>.supabase.co
supabase-anon-key       Supabase anon/public JWT
supabase-service-key    Supabase service-role key (never expose to clients)

moss-api-key            Moss retrieval API key
moss-base-url           Moss base URL, e.g. https://api.moss.ai

tfy-api-key             TrueFoundry platform API key
tfy-gateway-url         TrueFoundry AI Gateway base URL

anthropic-api-key       Anthropic API key (Claude via TFY gateway)
qwen-api-key            Qwen API key
minimax-api-key         Minimax API key

unsiloed-api-key        Unsiloed document parsing key

livekit-url             wss://<your-project>.livekit.cloud
livekit-api-key         LiveKit API key (used to mint room tokens server-side)
livekit-api-secret      LiveKit API secret (server-side only)

aws-access-key-id       AWS IAM access key
aws-secret-access-key   AWS IAM secret key
s3-bucket               S3 bucket name for raw uploaded files

slack-webhook-url       (OPTIONAL) Incoming webhook for lead-routing notifications.
                        If unset the gateway logs the lead and skips the Slack post
                        rather than erroring.

default-org-id          UUID of the single demo organisation, e.g.
                        00000000-0000-0000-0000-000000000001
──────────────────────────────────────────────────────────────────────────────
```

> **`LIVEKIT_STT_MODEL` is optional.** It has a sensible default
> (`assemblyai/universal-streaming`) and does not need to be stored as a secret;
> set it as a plain env var on the `relay-agent` service if you want to override it.
>
> **No Deepgram key.** STT is handled by LiveKit Inference. There is no
> `DEEPGRAM_API_KEY` in the production design.

---

## 4. Part C — Deploy order

### Prerequisites

```bash
pip install truefoundry

tfy login --host <your-truefoundry-host>

# Set these in your shell before running any deploy script.
export WORKSPACE_FQN=<cluster>:<workspace>        # e.g. tfy-use1:relay
export GATEWAY_HOST=relay-gateway.<your-tfy-domain>

# One or more origins, comma-separated, NO trailing slash.
# Example: a single prod origin or dev + prod:
export FRONTEND_ORIGIN=https://relay-omega-five.vercel.app
# Multi-origin example:
# export FRONTEND_ORIGIN=http://localhost:3000,https://relay-omega-five.vercel.app
```

> **CORS:** `FRONTEND_ORIGIN` may be a comma-separated list
> (e.g. `http://localhost:3000,https://relay-omega-five.vercel.app`).
> The gateway parses it into a list and passes each origin to FastAPI's
> `CORSMiddleware`. Never use `*` with credentials — this would break
> cookie/JWT auth from the browser.

### Sanity-check the image locally (optional but recommended)

```bash
docker build -t relay backend/
docker run --rm relay python -c "import relay.gateway.app; print('ok')"
```

### Step 1 — Register and run the migration job

```bash
cd backend
python deploy/deploy_migrate.py   # registers the Job in TrueFoundry
```

Then in the TrueFoundry UI (or via `tfy job trigger`):
- Trigger **relay-migrate** once.
- Wait for it to complete successfully before proceeding.
- This runs `alembic upgrade head` against the **direct** DSN (port 5432) and:
  - Enables `pgvector`
  - Creates all tables (organizations, members, documents, chunks, sessions,
    utterances, memory_items, leads, audit_log)
  - Creates ivfflat vector indexes
  - Creates the `relay_app` role
  - Applies org-isolation RLS policies on every tenant table

> **Do not retry** migration jobs automatically — Alembic DDL is not idempotent for
> all operations. The `relay-migrate` job is configured with `retries=0`.

### Step 2 — Deploy the gateway

```bash
python deploy/deploy_gateway.py
```

- Builds the image from `backend/Dockerfile`.
- Starts `uvicorn relay.gateway.app:app --host 0.0.0.0 --port 8000`.
- Exposes port 8000 publicly at `GATEWAY_HOST` (HTTPS + WSS on the same host/port).
- Health probe: `GET /health` → `{"status": "ok"}` (both liveness and readiness).
- Env references all `tfy-secret://relay/*` secrets (see the scripts for the full
  mapping).

### Step 3 — Deploy the agent worker

```bash
python deploy/deploy_agent.py
```

- Starts `python -m relay.agent.worker start` (production mode — connects to
  `LIVEKIT_URL`).
- No inbound port; the worker dials **out** to LiveKit Cloud.
- Joins rooms created by `POST /api/v1/sessions` and transcribes audio via
  LiveKit STT Inference.
- Scale: one replica handles a bounded number of concurrent rooms. Increase
  `replicas` or add a TrueFoundry autoscaler for higher concurrency.

### Step 4 — Deploy the ingestion worker

```bash
python deploy/deploy_ingest.py
```

- Starts `arq relay.ingestion.worker.WorkerSettings`.
- No inbound port; consumes the arq queue from Redis.
- Idempotent by `document_id` — re-ingesting replaces, never duplicates.
- Pipeline: S3 download → Unsiloed parse → chunk → embed (TFY gateway) →
  write to Moss + pgvector → set `document.status = ready`.

### Step 5 — Seed demo data (first deploy only)

Run locally with access to the direct DSN, or as an additional TrueFoundry Job:

```bash
# From the backend/ directory, with DATABASE_URL pointing at the direct DSN:
python -m relay.seed.northwind
# If TFY embeddings creds are absent during seeding:
python -m relay.seed.northwind --fake-embeddings
```

---

## 5. Part D — Frontend (Vercel)

### Import

1. In the Vercel dashboard click **Add New Project** and import the repository.
2. Set **Root Directory** to `frontend/`.
3. Set **Framework Preset** to **Vite** (Vercel auto-detects from `vercel.json`).

`frontend/vercel.json` configures:

```json
{
  "buildCommand": "tsc -b && vite build",
  "outputDirectory": "dist",
  "rewrites": [{ "source": "/(.*)", "destination": "/index.html" }]
}
```

The rewrite ensures all client-side routes (React Router) are served from
`index.html` — required for a Vite SPA.

### Environment variables (Production + Preview)

Set all of these in **Project Settings → Environment Variables**:

| Variable | Value | Notes |
|----------|-------|-------|
| `VITE_DEMO_MODE` | `false` | `true` = in-browser mock, no network calls |
| `VITE_BACKEND_URL` | `https://<GATEWAY_HOST>` | No trailing slash. REST (`/api/v1`) and WSS (`/ws`) are derived automatically by `src/config.ts` |
| `VITE_LIVEKIT_URL` | `wss://<your-project>.livekit.cloud` | Used by the frontend LiveKit client SDK to join rooms |
| `VITE_SUPABASE_URL` | `https://<project-ref>.supabase.co` | |
| `VITE_SUPABASE_ANON_KEY` | `<anon key>` | Public key — safe for the browser |

> **Do NOT set** `VITE_API_BASE` or `VITE_WS_BASE` in production.
> `frontend/src/config.ts` derives `API_BASE` as `${VITE_BACKEND_URL}/api/v1`
> and builds WebSocket URLs by replacing the `http(s)` scheme with `ws(s)`
> from `VITE_BACKEND_URL`. Overriding these breaks the auto-derivation.
>
> **Not `NEXT_PUBLIC_*`:** Relay's frontend is a **Vite** SPA, not Next.js.
> All client-visible env vars must be prefixed `VITE_`.

### CORS alignment

Ensure the gateway's `FRONTEND_ORIGIN` equals the deployed Vercel origin
(e.g. `https://relay-omega-five.vercel.app`). Mismatch causes CORS failures on
REST requests and WebSocket upgrade rejections.

When Vercel generates a preview-deployment URL (e.g.
`https://relay-git-feat-xyz.vercel.app`) you can add it to `FRONTEND_ORIGIN` as a
comma-separated entry and redeploy the gateway, or set a stable custom domain on
Vercel and use that.

---

## 6. Env-var matrix

### Frontend (Vercel) — `VITE_*` only

| Variable | Required | Example | Notes |
|----------|----------|---------|-------|
| `VITE_DEMO_MODE` | Yes | `false` | `true` enables in-browser mock |
| `VITE_BACKEND_URL` | Yes | `https://relay-gateway.tfy.example.com` | Derives REST base and WS URL |
| `VITE_LIVEKIT_URL` | Yes | `wss://myproj.livekit.cloud` | LiveKit room join URL for browser SDK |
| `VITE_SUPABASE_URL` | Yes | `https://abc123.supabase.co` | |
| `VITE_SUPABASE_ANON_KEY` | Yes | `eyJ...` | Anon key only |

### Backend — TrueFoundry service env vars

All values below reference `tfy-secret://relay/<key>` in the deploy scripts unless
noted. Sourced into Python via `relay.config.Settings` (pydantic-settings).

| Env var | Services | Required | Notes |
|---------|----------|----------|-------|
| `DATABASE_URL` | gateway, agent, ingest | Yes | Pooled DSN (port 6543), asyncpg driver |
| `DATABASE_URL` (direct) | migrate | Yes | Direct DSN (port 5432) — migrate job only |
| `REDIS_URL` | gateway, agent, ingest | Yes | arq queue and cache |
| `SUPABASE_URL` | gateway | Yes | JWT JWKS issuer |
| `SUPABASE_ANON_KEY` | gateway | Yes | |
| `SUPABASE_SERVICE_KEY` | gateway | Yes | Server-side only |
| `MOSS_API_KEY` | gateway, agent, ingest | Yes | Primary retrieval |
| `MOSS_BASE_URL` | gateway, agent, ingest | Yes | Default: `https://api.moss.ai` |
| `TFY_API_KEY` | gateway, agent, ingest | Yes | TrueFoundry gateway auth |
| `TFY_GATEWAY_URL` | gateway, agent, ingest | Yes | LLM gateway base URL |
| `ANTHROPIC_API_KEY` | gateway, agent | Yes | Claude via TFY gateway |
| `QWEN_API_KEY` | gateway, agent | Yes | Qwen via TFY gateway |
| `MINIMAX_API_KEY` | gateway, agent | Yes | Minimax via TFY gateway |
| `LLM_MODEL` | gateway, agent | No | Default: `claude`. Options: `claude`, `qwen`, `minimax` |
| `UNSILOED_API_KEY` | ingest | Yes | Document parsing |
| `LIVEKIT_URL` | gateway, agent | Yes | `wss://<proj>.livekit.cloud` |
| `LIVEKIT_API_KEY` | gateway, agent | Yes | Token minting (server-side) |
| `LIVEKIT_API_SECRET` | gateway, agent | Yes | Server-side only, never expose |
| `LIVEKIT_STT_MODEL` | agent | No | Default: `assemblyai/universal-streaming`. Override to switch STT model; billed on LiveKit credits |
| `AWS_ACCESS_KEY_ID` | gateway, ingest | Yes | S3 presigned uploads + fetch |
| `AWS_SECRET_ACCESS_KEY` | gateway, ingest | Yes | |
| `AWS_REGION` | gateway, ingest | No | Default: `us-east-1` |
| `S3_BUCKET` | gateway, ingest | Yes | Raw file bucket name |
| `SLACK_WEBHOOK_URL` | gateway | No | Lead-routing notifications. If unset, gateway logs the lead and skips the Slack post instead of erroring |
| `FRONTEND_ORIGIN` | gateway | Yes | Comma-separated list of allowed origins, no trailing slash. Drives CORS and WS origin check |
| `DEFAULT_ORG_ID` | gateway, agent, ingest | Yes | Demo org UUID; used by seed and local dev |
| `EMBEDDING_DIM` | ingest | No | Default: `1024`. Must match the embeddings model |
| `APP_DB_ROLE` | gateway | No | Default: `relay_app`. Postgres role subject to RLS |

---

## 7. Verification checklist

### 7.1 Gateway health

```bash
curl https://<GATEWAY_HOST>/health
# Expected: {"status":"ok"}
```

### 7.2 Migration completed

In Supabase SQL editor, confirm key tables and RLS exist:

```sql
-- Tables present
select tablename from pg_tables
where schemaname = 'public'
order by tablename;
-- Expect: audit_log, chunks, documents, leads, memory_items,
--         organizations, sessions, utterances, members

-- RLS enabled on tenant tables
select tablename, rowsecurity
from pg_tables
where schemaname = 'public' and rowsecurity = true;

-- pgvector extension
select extname from pg_extension where extname = 'vector';
```

### 7.3 Demo seed loaded

```bash
curl -s https://<GATEWAY_HOST>/api/v1/documents \
  -H "Authorization: Bearer <service-or-demo-jwt>" \
  | python -m json.tool | grep '"status"'
# Expect one or more "status": "ready" entries
```

### 7.4 Manual query renders a card

```bash
curl -s -X POST https://<GATEWAY_HOST>/api/v1/query \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"query": "What are the available products?", "session_id": null}' \
  | python -m json.tool
# Expect a card object with citations and a non-null "answer" field.
# A null "card" means no grounding found — that is correct behaviour, not an error.
```

### 7.5 Document upload → ingestion → ready

1. Upload a document via `POST /api/v1/documents` (presigned S3 flow).
2. Poll `GET /api/v1/documents/{id}` until `"status": "ready"`.
3. Issue a query related to the document's content and verify a cited card is returned.

If `status` stays `processing` beyond ~60 s, check `relay-ingest` logs in TrueFoundry
for adapter or S3 errors.

### 7.6 WebSocket event stream

Open a WebSocket connection to `wss://<GATEWAY_HOST>/ws/sessions/{session_id}` (with
a valid JWT in the `Authorization` header or as a query param per `API_SPEC.md`) and
confirm you receive `session.status` on connection and `card.new` / `card.update`
events when a query is triggered.

### 7.7 Frontend loads and authenticates

Navigate to the Vercel URL:
- Supabase auth login page renders.
- After sign-in, the dashboard loads without CORS errors in the browser console.
- `VITE_DEMO_MODE=false` is confirmed (no "Demo Mode" banner in the UI).

---

## 8. Known gaps

- **Browser LiveKit audio is now wired** via `livekit-client` in the frontend
  (room join + mic capture). Confirm that `relay-agent` is correctly dispatched
  to the room created by `POST /api/v1/sessions` — the agent must receive a job
  from the LiveKit dispatcher before it can transcribe audio. If cards do not appear
  after speaking, check `relay-agent` logs for room join events.

- **Agent STT uses LiveKit Inference.** `relay/agent/worker.py` passes
  `settings.livekit_stt_model` (env `LIVEKIT_STT_MODEL`, default
  `assemblyai/universal-streaming`) to the `AgentSession`; LiveKit routes and bills STT
  on the existing `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`. There is no
  `livekit-plugins-deepgram` dependency and no `DEEPGRAM_API_KEY`.

- **CORS parses a list.** `relay/gateway/app.py` passes `settings.cors_origins` to
  `CORSMiddleware`. `FRONTEND_ORIGIN` may be a comma-separated list; the parser splits
  on commas, strips whitespace and trailing slashes, and never uses `*` with credentials.

- **Rate limiting is in-process.** The sliding-window token buckets in
  `RateLimitMiddleware` are per-process and do not share state across gateway
  replicas. For horizontal scale, replace with a Redis-backed rate limiter.

- **`relay-migrate` retries=0.** Alembic DDL is not idempotent for all operations.
  Never configure automatic retries on this job. Trigger it manually after each
  schema change and verify success before rolling out new service versions.

---

## 9. Related docs

- [`CLAUDE.md`](../CLAUDE.md) — project overview, architecture invariants, conventions
- [`docs/TECHNICAL_DESIGN.md`](TECHNICAL_DESIGN.md) — component architecture, latency
  budget, retrieval pipeline, WebSocket hub design
- [`docs/BUILD_SPEC_AND_MASTER_PROMPT.md`](BUILD_SPEC_AND_MASTER_PROMPT.md) — full
  build order, sponsor integrations, auth flow, step-by-step setup

# Relay deployment runbook — TrueFoundry + Vercel + Supabase

Deploys the **`backend/relay`** package (the complete implementation) as three TrueFoundry
services from one shared image, with the React/Vite frontend on Vercel and Postgres+Auth on
Supabase. See the full plan at `docs/`/your plan file for context.

> `backend/relay` is a **monolith** — retrieval + orchestrator run in-process in the
> gateway. There is **no separate retrieval service** (unlike the `docker/` skeleton), so
> there is no `RETRIEVAL_URL`.

## Services (one image, different start commands)

| TFY service | Command | Ingress | Purpose |
|---|---|---|---|
| `relay-gateway` | `uvicorn relay.gateway.app:app --port 8000` | public (`/`, WS on same host) | REST + WebSocket hub |
| `relay-agent` | `python -m relay.agent.worker start` | none | LiveKit room worker (STT + trigger) |
| `relay-ingest` | `arq relay.ingestion.worker.WorkerSettings` | none | async document ingestion |
| `relay-migrate` | `alembic -c alembic.ini upgrade head` | Job (manual) | one-off schema/RLS migration |

## 1. Provision dependencies

- **Supabase:** create the project; `create extension if not exists vector;`; enable Email +
  Google auth with the Vercel domain as redirect. Note: the **pooled** DSN (port 6543) for
  the app `DATABASE_URL`, and the **direct** DSN (port 5432) for migrations. JWTs are verified
  via Supabase JWKS (RS256) using `SUPABASE_URL`; no JWT secret needed in prod. A custom
  claims hook is optional — `relay.auth.deps._bootstrap_principal` creates the org + owner
  membership and backfills `org_id` on first sign-in.
- **AWS S3:** private bucket + CORS allowing `PUT`/`GET` from the Vercel origin + IAM creds.
- **Redis:** any Redis reachable by the services (TFY add-on / Upstash / ElastiCache).
- **LiveKit Cloud:** project URL + API key/secret.

## 2. Create TrueFoundry secrets (group `relay`)

Create each as `tfy-secret://relay/<key>`:

```
database-url            # POOLED Supabase DSN (6543), postgresql+asyncpg://...
database-url-direct     # DIRECT Supabase DSN (5432) — migrate job only
redis-url
supabase-url  supabase-anon-key  supabase-service-key
moss-api-key  moss-base-url
tfy-api-key  tfy-gateway-url
anthropic-api-key  qwen-api-key  minimax-api-key
unsiloed-api-key
livekit-url  livekit-api-key  livekit-api-secret
# STT runs through LiveKit Inference (billed on the LiveKit key) — NO Deepgram secret.
# LIVEKIT_STT_MODEL has a default (assemblyai/universal-streaming), so it's optional.
aws-access-key-id  aws-secret-access-key  s3-bucket
slack-webhook-url       # OPTIONAL — if unset, lead routing skips Slack and logs
default-org-id          # the single demo org uuid (matches DEFAULT_ORG_ID)
```

## 3. Deploy

```bash
pip install truefoundry
tfy login --host <your-truefoundry-host>
export WORKSPACE_FQN=<cluster>:<workspace>            # e.g. tfy-use1:relay
export GATEWAY_HOST=relay-gateway.<your-tfy-domain>
export FRONTEND_ORIGIN=https://<your-app>.vercel.app  # no trailing slash; comma-separated list ok (e.g. +http://localhost:3000)

cd backend
python deploy/deploy_migrate.py     # register, then TRIGGER once in the UI
python deploy/deploy_gateway.py
python deploy/deploy_agent.py
python deploy/deploy_ingest.py
```

Seed demo data once (locally against the DSN, or as an extra job):
`python -m relay.seed.northwind`  (add `--fake-embeddings` only if TFY embeddings creds are absent).

## 4. Frontend (Vercel)

Import the repo, root directory `frontend/`, preset **Vite** (`frontend/vercel.json` sets the
build command, `dist` output, and the SPA rewrite). Env (Production + Preview):

```
VITE_DEMO_MODE=false
VITE_BACKEND_URL=https://<GATEWAY_HOST>
VITE_SUPABASE_URL=https://<project-ref>.supabase.co
VITE_SUPABASE_ANON_KEY=<anon key>
```

Leave `VITE_API_BASE` / `VITE_WS_BASE` unset — `frontend/src/config.ts` derives the
`/api/v1` REST base and the `wss://` WebSocket URL from `VITE_BACKEND_URL` automatically.
Ensure the gateway's `FRONTEND_ORIGIN` equals the deployed Vercel origin (CORS + WS origin
check).

## Local image sanity check

```bash
docker build -t relay backend/
docker run --rm relay python -c "import relay.gateway.app; print('ok')"
```

## STT

STT runs through **LiveKit Inference** — the agent worker passes a model string
(`LIVEKIT_STT_MODEL`, default `assemblyai/universal-streaming`) to the `AgentSession`,
and LiveKit routes/bills it against the existing `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`.
There is no separate STT provider account, plugin package, or `DEEPGRAM_API_KEY`.

## Known gaps

- **Browser LiveKit audio is now wired** (`frontend` uses `livekit-client` to join the
  room and publish the mic when `VITE_DEMO_MODE=false`). Confirm `VITE_LIVEKIT_URL` is set
  on Vercel and that the `relay-agent` is dispatched to the room created by `POST /sessions`
  (automatic dispatch when `agent_name` is unset; the gateway stamps room metadata so the
  agent reads the right org/mode).
- Required secrets (other than Slack and `LIVEKIT_STT_MODEL`) must be set or the adapters
  raise `RuntimeError` at construction and the service won't boot.

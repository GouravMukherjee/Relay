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
deepgram-api-key
aws-access-key-id  aws-secret-access-key  s3-bucket
slack-webhook-url
default-org-id          # the single demo org uuid (matches DEFAULT_ORG_ID)
```

## 3. Deploy

```bash
pip install truefoundry
tfy login --host <your-truefoundry-host>
export WORKSPACE_FQN=<cluster>:<workspace>            # e.g. tfy-use1:relay
export GATEWAY_HOST=relay-gateway.<your-tfy-domain>
export FRONTEND_ORIGIN=https://<your-app>.vercel.app  # must equal the Vercel origin

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

## Known gaps

- **Browser LiveKit audio is not wired** in the frontend (it uses the WS event transport, no
  mic capture / room join). The manual-query path works end-to-end; true live-audio needs
  LiveKit client-SDK wiring + agent dispatch to the room.
- Confirm the `relay-agent` is dispatched to the room created by `POST /sessions`.
- Every adapter raises `RuntimeError` at construction if its required secret is blank — all
  secrets above must be set for the services to boot.

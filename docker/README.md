# Relay Backend — Docker & Deploy

Everything needed to build and run the Relay backend, locally with Docker Compose
and in production on **TrueFoundry**. Implements the topology in
`docs/TECHNICAL_DESIGN.md` (§2 architecture, §5 stack).

## What's in here

```
docker/
├── Dockerfile              # one shared image for all 3 services
├── docker-compose.yml      # local stack: postgres(pgvector) + gateway + retrieval + worker
├── requirements.txt        # shared Python deps
├── .env.example            # copy to .env, fill in keys
├── .dockerignore
├── init-db/
│   └── 01_schema.sql       # pgvector + full schema (docs/DATA_MODEL.md)
├── app/                    # runnable skeleton (health works; business logic = TODOs)
│   ├── config.py           # env settings
│   ├── db.py               # asyncpg pool
│   ├── gateway.py          # FastAPI REST + WebSocket hub  (port 8000, PUBLIC)
│   ├── retrieval.py        # Moss + pgvector fallback       (port 8001, INTERNAL)
│   └── worker.py           # LiveKit agent worker           (no port, ALWAYS-ON)
└── deploy/                 # TrueFoundry deploy specs (one per service)
    ├── deploy_gateway.py
    ├── deploy_retrieval.py
    └── deploy_worker.py
```

## The three services

| Service | Image command | Port | Exposure | Role |
|---|---|---|---|---|
| **gateway** | `uvicorn app.gateway:app` | 8000 | **Public** | REST + WebSocket hub. The frontend (Vercel) talks here. |
| **retrieval** | `uvicorn app.retrieval:app` | 8001 | Internal | Moss top-k, pgvector fallback. Called by gateway/worker. |
| **worker** | `python -m app.worker` | — | None (outbound) | Always-on LiveKit agent: audio → STT → trigger → retrieval. |

All three run the **same image** — only the start command differs.

External dependencies (not containers you run): **LiveKit Cloud** (audio),
**Moss** (retrieval index), **Claude / Deepgram / Unsiloed** (APIs), and
**Postgres + pgvector** (bundled locally; managed in prod).

---

## Run locally

```bash
cd docker
cp .env.example .env          # fill in MOSS/LIVEKIT/ANTHROPIC/DEEPGRAM keys
docker compose up --build
```

Verify:

```bash
curl localhost:8000/healthz   # gateway  -> {"status":"ok",...}
curl localhost:8000/readyz    # gateway  -> db connectivity
curl localhost:8001/healthz   # retrieval
docker compose logs -f worker # worker heartbeat
```

Postgres comes up with the full schema already applied (`init-db/01_schema.sql`
runs on first boot). Wipe and re-seed with `docker compose down -v`.

> Skeleton scope: health/readiness, the REST routes from `API_SPEC.md`, and the
> session WebSocket are wired and runnable. The retrieval/synthesis/STT bodies
> are marked `TODO(T1.x)` against the Phase-1 tasks in `docs/BUILD_PLAN.md`.

---

## Deploy to TrueFoundry

TrueFoundry is a PaaS over Kubernetes: you hand it this repo + a Python spec, it
builds the image, pushes it, and runs it as an always-on, autoscaling Service —
which is exactly why the LiveKit worker and the WebSocket hub work here and not
on Vercel.

### 1. Provision Postgres (once)

Use managed Postgres with pgvector — **Neon**, **Supabase**, or **RDS**. Enable
the extension and load the schema:

```bash
psql "$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql "$DATABASE_URL" -f init-db/01_schema.sql
```

Grab the connection string for the next step.

### 2. Create secrets (once)

In the TrueFoundry UI → **Secrets**, create a group `relay` with these keys
(the deploy specs reference them as `tfy-secret://relay/<key>`):

| Secret key | Value |
|---|---|
| `database-url` | the managed Postgres connection string |
| `anthropic-api-key` | Claude API key |
| `moss-api-key`, `moss-endpoint` | Moss credentials |
| `livekit-url`, `livekit-api-key`, `livekit-api-secret` | LiveKit Cloud |
| `deepgram-api-key` | STT |
| `unsiloed-api-key` | doc parsing |

### 3. Deploy the services

```bash
pip install truefoundry
tfy login --host <your-truefoundry-host>

export WORKSPACE_FQN=<cluster>:<workspace>      # e.g. tfy-use1:relay
export GATEWAY_HOST=relay-gateway.<your-tfy-domain>

# run from the docker/ directory (build context = .)
python deploy/deploy_retrieval.py    # internal first
python deploy/deploy_worker.py       # always-on worker
python deploy/deploy_gateway.py      # public gateway last
```

Each command builds the image from `./Dockerfile` and rolls out one Service.
Watch logs/status in the TrueFoundry dashboard.

### 4. Point the frontend at the gateway

Set the frontend's API base URL (on Vercel) to the gateway host from step 3,
e.g. `https://relay-gateway.<your-tfy-domain>`. WebSockets use the same host
(`wss://.../ws/sessions/{id}`).

### Topology

```
Vercel ── React dashboard ──HTTPS/WSS──▶ relay-gateway (public, :8000)
                                              │  intra-cluster
                                              ▼
                                        relay-retrieval (internal, :8001) ──▶ Moss
                                              ▲
LiveKit Cloud ──audio──▶ relay-worker (always-on) ──┘   ──▶ Claude / Deepgram
                                              │
                  all services ───────────────┴──▶ Managed Postgres + pgvector
```

Keep gateway, retrieval, and worker in the **same workspace/region** so internal
calls stay sub-millisecond and the latency budget (TDD §4) holds.

---

## Notes & next steps

- **Scaling:** bump `replicas` in `deploy/*.py` (or add an autoscaler) per
  service. The worker scales with concurrent live calls; gateway with WS clients.
- **Image registry:** TrueFoundry builds and pushes to its configured registry
  automatically — no manual `docker push` needed.
- **Auth:** `API_SPEC.md` omits auth for the hackathon. Add a bearer token at the
  gateway before any real deployment.
- **Fill the TODOs:** implement Phase-1 tasks (`docs/BUILD_PLAN.md` T1.1–T1.6) in
  `app/worker.py`, `app/retrieval.py`, and `app/gateway.py`.

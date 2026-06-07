# Deploy the Relay backend on EC2 / a Docker VM

Runs the full backend (Redis + gateway + agent + ingest) from one image with
`docker-compose.yml`. The gateway is published **directly on port 8000** — no
reverse proxy. Postgres + Auth stay on **Supabase**; S3 stays on **AWS**; LLM via
the **TrueFoundry gateway**; STT via **LiveKit Inference**. Nothing here needs a
local Postgres.

```
Internet ──8000──▶ gateway:8000 (REST + WS)
                    ├─ redis (queue/cache)
                    ├─ agent  → LiveKit Cloud (outbound)
                    └─ ingest → S3 / Unsiloed / Moss
gateway/agent/ingest ──▶ Supabase Postgres (DATABASE_URL)  ·  S3  ·  TFY gateway
```

## 1. Provision the VM

- Ubuntu 22.04+ EC2 (t3.small/medium is plenty). Attach an **Elastic IP**.
- **Security group inbound:** `22` (your IP only) and `8000` (`0.0.0.0/0`, the
  gateway). No other ports.

## 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker   # run docker without sudo
docker compose version                            # v2 plugin ships with it
```

## 3. Get the code + secrets

```bash
git clone https://github.com/GouravMukherjee/Relay.git
cd Relay/backend
cp .env.example .env
$EDITOR .env            # fill every key (or scp your filled .env up)
```

`.env` essentials for a VM deploy:
- `DATABASE_URL=postgresql+asyncpg://postgres.<ref>:<pwd>@<region>.pooler.supabase.com:6543/postgres`
  — use the **pooled 6543** transaction pooler for the app (the engine disables the
  statement cache there automatically). `5432` (session pooler) also works.
- `FRONTEND_ORIGIN` — your Vercel origin, **no trailing slash**; comma‑separate to
  also allow localhost, e.g. `https://relay-omega-five.vercel.app,http://localhost:5173`.
- `REDIS_URL` is overridden to `redis://redis:6379/0` by the compose file — leave
  the `.env` value as-is.
- Supabase / LiveKit / TFY / Anthropic / Unsiloed / AWS(S3) keys filled. Moss/Slack
  optional (adapters that need a missing key only fail when that feature is used;
  Slack just skips).

## 4. Launch

```bash
docker compose up -d --build
```

Apply the DB schema once (idempotent; safe even if already migrated):

```bash
docker compose run --rm migrate
```

## 5. Verify

```bash
curl http://localhost:8000/health            # -> {"status":"ok"}
docker compose ps
docker compose logs -f gateway               # and: agent, ingest
```

A deeper check (auth + Supabase + RLS), from any machine with the JWT secret:
mint an HS256 token with `SUPABASE_JWT_SECRET` and `GET http://<host>:8000/api/v1/documents`
→ `200 {"documents":[]}` (matches the local smoke test).

## 6. Point the frontend at it (Vercel)

The frontend is served over **HTTPS** and the gateway is plain **HTTP** on `:8000`,
so the browser would block a direct `https → http` call (mixed content). Route REST
through the **Vercel rewrite proxy** instead — `frontend/vercel.json` proxies
`/api/*` to the backend server-side, so the browser only ever talks to the HTTPS
Vercel origin. Set the rewrite destination to your backend and configure:

```jsonc
// frontend/vercel.json
{ "source": "/api/:path*", "destination": "http://<elastic-ip>:8000/api/:path*" }
```
```
# Vercel env (Production + Preview), then redeploy
VITE_BACKEND_URL=http://<elastic-ip>:8000
VITE_API_BASE=/api/v1                              # REST via the same-origin proxy
VITE_SUPABASE_URL=https://<ref>.supabase.co
VITE_SUPABASE_ANON_KEY=<anon key>
```

> **WebSockets / live audio caveat:** a browser on an HTTPS page can't open a plain
> `ws://` connection, and Vercel can't proxy WebSockets. So the live transcript/card
> stream (and LiveKit audio) needs TLS in front of the gateway. Without a reverse
> proxy here, terminate TLS at a **cloud load balancer** (e.g. an AWS ALB or
> Cloudflare in front of `:8000`) and point `VITE_WS_BASE=wss://…` at it. REST +
> manual queries work fine over the HTTP proxy above; only the realtime WS path
> needs the TLS endpoint.

Ensure the gateway's `FRONTEND_ORIGIN` includes the exact Vercel origin (CORS + WS
origin check).

## Operate

```bash
# update to latest code
git pull && docker compose up -d --build
# restart one service
docker compose restart gateway
# tail logs / stop everything
docker compose logs -f
docker compose down
```

## Notes & caveats

- **Agent / live audio:** `agent` runs `relay.agent.worker start` and uses LiveKit
  Inference STT (`LIVEKIT_STT_MODEL`). If `livekit-agents` in the image predates the
  inference-STT string form, check `docker compose logs agent` and pin a newer
  `livekit-agents` in `requirements.txt`. The REST/WS + manual-query path does not
  depend on the agent.
- **Secrets** live only in `.env` on the VM (gitignored; never baked into the image).
  Restrict the file: `chmod 600 .env`.
- **Scale later:** this is a single box. For HA, move to the TrueFoundry path
  (`deploy/*.py`, see `docs/DEPLOYMENT.md`) or run multiple VMs behind a load balancer
  and swap the in-process rate limiter for a Redis-backed one.
```

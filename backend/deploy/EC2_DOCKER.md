# Deploy the Relay backend on EC2 / a Docker VM

Runs the full backend (Redis + gateway + agent + ingest + Caddy auto‑HTTPS) from
one image with `docker-compose.prod.yml`. Postgres + Auth stay on **Supabase**;
S3 stays on **AWS**; LLM via the **TrueFoundry gateway**; STT via **LiveKit
Inference**. Nothing here needs a local Postgres.

```
Internet ──443/80──▶ Caddy (TLS) ──▶ gateway:8000 (REST + WS)
                                      ├─ redis (queue/cache)
                                      ├─ agent  → LiveKit Cloud (outbound)
                                      └─ ingest → S3 / Unsiloed / Moss
gateway/agent/ingest ──▶ Supabase Postgres (DATABASE_URL)  ·  S3  ·  TFY gateway
```

## 1. Provision the VM

- Ubuntu 22.04+ EC2 (t3.small/medium is plenty). Attach an **Elastic IP**.
- **Security group inbound:** `22` (your IP only), `80` and `443` (`0.0.0.0/0`,
  for Caddy + ACME). No other ports — gateway 8000 is internal to the compose net.
- A hostname that resolves to the IP. Either a real domain (`api.yourdomain.com`)
  or, for a quick demo with no DNS, use **`<elastic-ip>.nip.io`** (resolves to the
  IP automatically; Caddy can still get a Let's Encrypt cert for it).

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
export RELAY_DOMAIN=api.yourdomain.com        # or <elastic-ip>.nip.io
docker compose -f docker-compose.prod.yml up -d --build
```

Apply the DB schema once (idempotent; safe even if already migrated):

```bash
docker compose -f docker-compose.prod.yml run --rm migrate
```

## 5. Verify

```bash
curl https://$RELAY_DOMAIN/health            # -> {"status":"ok"}
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f gateway   # and: agent, ingest, caddy
```

A deeper check (auth + Supabase + RLS), from any machine with the JWT secret:
mint an HS256 token with `SUPABASE_JWT_SECRET` and `GET https://$RELAY_DOMAIN/api/v1/documents`
→ `200 {"documents":[]}` (matches the local smoke test).

## 6. Point the frontend at it (Vercel)

Set on Vercel (Production + Preview) and redeploy:
```
VITE_DEMO_MODE=false
VITE_BACKEND_URL=https://api.yourdomain.com      # == RELAY_DOMAIN, https
VITE_LIVEKIT_URL=wss://<your-project>.livekit.cloud
VITE_SUPABASE_URL=https://<ref>.supabase.co
VITE_SUPABASE_ANON_KEY=<anon key>
```
Ensure the gateway's `FRONTEND_ORIGIN` includes the exact Vercel origin (CORS + WS
origin check). REST resolves to `<VITE_BACKEND_URL>/api/v1` and WS to `wss://…/ws/...`
automatically (see `frontend/src/config.ts`).

## Operate

```bash
# update to latest code
git pull && docker compose -f docker-compose.prod.yml up -d --build
# restart one service
docker compose -f docker-compose.prod.yml restart gateway
# tail logs / stop everything
docker compose -f docker-compose.prod.yml logs -f
docker compose -f docker-compose.prod.yml down
```

## Notes & caveats

- **HTTPS is required** because the frontend is HTTPS — browsers block HTTPS→HTTP.
  Caddy handles TLS via `RELAY_DOMAIN`; a bare IP without a hostname can't get a
  trusted cert (use a domain or `nip.io`).
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

# Relay — Demo Runbook

One checklist to stand up Relay and run the demo. Backend = Docker on the VM
(`api.riyanshomelab.com`); DB/Auth = Supabase; retrieval = Moss; LLM = Claude via the
TrueFoundry gateway; audio + STT = LiveKit. Frontend = Vite SPA on Vercel.

## 0. Prerequisites (one-time)
- VM with Docker + ports **80/443** reachable (DNS A-record `api.riyanshomelab.com` → VM,
  router port-forward) — Caddy needs them for TLS.
- `backend/.env` filled on the VM (`chmod 600 .env`). Required for the demo:
  `DATABASE_URL` (Supabase pooled, `postgresql+asyncpg://…`), `SUPABASE_URL`,
  `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`, `SUPABASE_JWT_SECRET`,
  `MOSS_PROJECT_ID`, `MOSS_PROJECT_KEY`, `TFY_API_KEY`, `TFY_GATEWAY_URL`,
  `TFY_MODEL=anthropic/claude-sonnet-4-5`, `LIVEKIT_URL/API_KEY/API_SECRET`,
  `FRONTEND_ORIGIN` (your Vercel origin(s), comma-sep, no trailing slash).
  Optional: `UNSILOED_API_KEY` + `AWS_*`/`S3_BUCKET` (only for live uploads),
  `SLACK_WEBHOOK_URL` (Intake routing).

## 1. Deploy the backend
```bash
cd Relay/backend && git pull
export RELAY_DOMAIN=api.riyanshomelab.com
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml run --rm migrate            # schema + RLS (idempotent)
docker compose -f docker-compose.prod.yml run --rm gateway python -m relay.seed.moss_index
curl https://api.riyanshomelab.com/health                              # {"status":"ok"}
docker compose -f docker-compose.prod.yml logs -f agent                # confirms LiveKit registration
```
The seed loads the Northwind KB into Moss (+documents/chunks in Postgres for the KB screen)
**and** Sarah Chen's memories into the Moss memory index (Desk).

## 2. Deploy the frontend (Vercel)
Set env (Production + Preview) and **redeploy** (Vite inlines at build time):
```
VITE_DEMO_MODE=false
VITE_BACKEND_URL=https://api.riyanshomelab.com
VITE_LIVEKIT_URL=wss://relay-ayfm1fbo.livekit.cloud
VITE_SUPABASE_URL=https://lepmbgtxjduuoiwvdaww.supabase.co
VITE_SUPABASE_ANON_KEY=<anon key>
```
Add the Vercel origin to Supabase Auth → URL Configuration, and ensure it is in the
backend `FRONTEND_ORIGIN`. For Google login: enable Google in Supabase + a Google OAuth
client with redirect `https://lepmbgtxjduuoiwvdaww.supabase.co/auth/v1/callback`.

## 3. Sign in
- Open the Vercel site → **Continue with Google** or email/**Create account**.
- A pre-made account exists: `demo@relay.app` / `RelayDemo!2026`.
- First sign-in auto-provisions the org + owner; the KB screen lists the seeded docs.

## 4. The demo flows

### Live (the headline) — grounded card from a question
- Open **Live**. Two ways to trigger:
  - **Typed (always works):** type "What's your uptime SLA?" → grounded card cites MSA.pdf.
  - **Spoken (LiveKit):** allow the mic, say the question → the agent transcribes (LiveKit
    Inference STT), retrieves from Moss, and a cited card appears.
- Good demo lines: "Are you SOC 2 compliant?", "What would Enterprise run us?",
  "Acme says they do the same for less." → each returns a cited card; ask something
  off-topic ("capital of France?") to show **grounded-or-silent** (no card).

### Desk — resolution citing doc + history
- Open **Desk** (customer = Sarah Chen). Ask "The CRM export sync issue is back."
- Card recalls her history (OAuth re-auth, Ticket #1023) **and** grounds on the FAQ/ticket
  → "re-authenticate the integration…", citing Ticket #1023 + FAQ.pdf.

### Intake — lead gen
- Open **Intake**. The **leads list / route-to-Slack / book** REST actions work, and a demo
  lead (Jordan Mraz) is seeded. **Caveat:** the live qualifying conversation → BANT → ICP
  score → auto-create lead is **driven by the in-browser mock** today; the backend does not
  yet synthesize a scored lead from a live call. Demo Intake from the seeded lead + routing.

## 5. Fallbacks (if something misbehaves live)
- **Mic/STT flaky:** use the typed query box — same retrieval+card path.
- **Moss hiccup:** retrieval degrades but stays grounded (no fabricated cards).
- **Worst case:** flip the frontend to `VITE_DEMO_MODE=true` (in-browser scripted demo, no
  backend) as a guaranteed-clean rehearsal of the exact beats.

## Verified vs. runtime-pending
- **Verified live (this build):** auth (ES256), Live manual query → cited card, Desk memory
  recall + resolution card, Moss tenant isolation, grounded-or-silent. `pytest` 24/3.
- **Pending real deploy + mic:** the spoken-audio round-trip (mic→LiveKit STT→agent→card);
  all plumbing (Redis cross-process hub, STT API form, dispatch) is verified, only the live
  room audio is exercisable on the deployed stack.

## Related docs
`DEPLOYMENT.md` · `backend/deploy/EC2_DOCKER.md` · `BUILD_SPEC_AND_MASTER_PROMPT.md` · `CLAUDE.md`

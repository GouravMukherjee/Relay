# Relay

**Ambient AI co-pilot that surfaces answers from your own knowledge — live, mid-conversation.**

Relay listens to a live call, and the instant a question or topic comes up, retrieves the answer from your team's documents (sub-10 ms via Moss) and renders a grounded, cited card on screen in under half a second. One engine, three modes: **Live** (co-pilot), **Desk** (support), **Intake** (lead gen).

Built for the YC × Moss Conversational AI Hackathon, June 2026.

---

## Architecture (short)

```
Audio (LiveKit) → STT → trigger → Moss retrieval (<10ms) → Claude synth → card → WebSocket → React UI
Ingestion: upload → Unsiloed parse → chunk → embed → Moss index + Postgres
```

Full detail in [`TECHNICAL_DESIGN.md`](./TECHNICAL_DESIGN.md).

## Stack

| Layer | Tool |
|-------|------|
| Audio transport | LiveKit |
| STT | streaming (Deepgram / Whisper-class) |
| Retrieval | **Moss** (<10 ms) |
| Doc parsing | Unsiloed |
| LLM | Claude (primary), Minimax (optional) |
| Backend | Python 3.11 · FastAPI · LiveKit Agents |
| DB | PostgreSQL 15 + pgvector |
| Frontend | React 18 + TypeScript + Vite |
| Deploy | AWS / TrueFoundry |

## Repo layout

```
.
├── CLAUDE.md                 # Claude Code anchor (points to docs below)
├── .claude/agents/           # subagent definitions
├── backend/
│   ├── gateway/              # FastAPI REST + WebSocket hub
│   ├── agent/                # LiveKit agent worker (STT, trigger)
│   ├── retrieval/            # Moss client + pgvector fallback
│   ├── ingestion/            # Unsiloed parse → chunk → embed → index
│   ├── orchestrator/         # Claude synthesis (grounded cards)
│   └── memory/               # cross-session facts
├── frontend/                 # React + TS card UI
├── docs/                     # PRD, TDD, API_SPEC, DATA_MODEL, etc.
└── data/demo/                # Northwind demo dataset
```

## Quickstart

```bash
# 1. clone + env
cp .env.example .env          # fill in MOSS_API_KEY, LIVEKIT_*, UNSILOED_*, ANTHROPIC_API_KEY, DATABASE_URL

# 2. database
docker compose up -d postgres
make migrate

# 3. backend
cd backend
pip install -r requirements.txt
make run-gateway              # FastAPI on :8000
make run-agent                # LiveKit agent worker

# 4. frontend
cd ../frontend
npm install
npm run dev                   # React + Vite on :5173

# 5. seed the demo dataset
make seed-demo                # ingests data/demo/* via the ingestion path
```

## Environment variables

See [`.env.example`](./.env.example). Required: `MOSS_API_KEY`, `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `UNSILOED_API_KEY`, `ANTHROPIC_API_KEY`, `DATABASE_URL`. Never commit `.env`.

## Demo

The rehearsed flow + dataset are in [`DEMO_SCRIPT.md`](./DEMO_SCRIPT.md). Run `make seed-demo` first, then follow the dry-run checklist.

## Docs index

[`PRD.md`](./PRD.md) · [`TECHNICAL_DESIGN.md`](./TECHNICAL_DESIGN.md) · [`API_SPEC.md`](./API_SPEC.md) · [`DATA_MODEL.md`](./DATA_MODEL.md) · [`BUILD_PLAN.md`](./BUILD_PLAN.md) · [`SCOPE.md`](./SCOPE.md) · [`DEMO_SCRIPT.md`](./DEMO_SCRIPT.md) · [`ADRs.md`](./ADRs.md) · [`TEST_PLAN.md`](./TEST_PLAN.md)

## License

Private — hackathon project. Not for redistribution.

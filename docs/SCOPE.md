# Relay — Scope & MVP Definition

**Status:** Draft for build · **Owner:** Product/Pitch · **Last updated:** June 2026

The single biggest failure mode for a 24h build is overbuilding. This doc is the guardrail. If a task isn't in **In scope**, it doesn't get built until the demo is bulletproof.

---

## The one thing that must work

> Speaking a question into a live call surfaces a **grounded, cited card** on screen in **<500 ms perceived** — pulled only from the uploaded knowledge base.

Everything else is negotiable. This is not.

---

## In scope (build first)

- **Relay Live** end-to-end: LiveKit audio → STT → trigger → Moss retrieval → Claude card → WebSocket → React render.
- **Ingestion path:** upload → Unsiloed parse → chunk → embed → Moss index (with pgvector fallback).
- **Demo dataset** fully ingested and query-ready (see DEMO_SCRIPT.md).
- **Card UI:** live transcript, grounded card with citation, latency badge.
- **Manual query fallback** (typed) — demo safety net if audio misbehaves.
- **Pre-cached demo queries** + recorded backup of the working flow.

## Stretch (only after Live is bulletproof)

- **Relay Desk** (Support): reactive query over docs + customer memory.
- **Relay Intake** (Lead Gen): qualifying flow → ICP score → lead route/notify.
- **Cross-session memory** beyond the demo stub.
- **Whisper-back TTS** (answer to an earpiece) instead of on-screen only.
- **Mode switcher** polish + card actions (pin/dismiss/expand).

## Explicitly out of scope (do not build for the hackathon)

- Auth / SSO / multi-tenant security hardening.
- Billing, plans, usage metering.
- Native Zoom/Meet/Teams plugins (beyond a basic audio path).
- Mobile apps.
- Analytics dashboards / reporting.
- Admin settings UI beyond uploading docs.
- Fine-tuning or custom model training.

---

## Decision rules

1. **Protect the critical path.** If a stretch item threatens M1 (mic→card) or M3 (reliable demo), drop it.
2. **One great mode > three weak ones.** Submit to extra tracks only if those modes are demo-clean.
3. **Grounded or silent.** The orchestrator returns "no card" rather than guessing. Hallucination loses judge trust instantly.
4. **Demo-driven.** Build toward the exact moments in DEMO_SCRIPT.md, not toward generality.

## Cut-line checkpoints

- **Hour 12:** If M1 isn't met, freeze scope to single-mode + harden. Abandon Desk/Intake.
- **Hour 18:** If a stretch mode isn't demoable, cut it from the pitch (keep code dormant).
- **Hour 22:** No new features. Hardening, pitch, and landing page only.

## Related docs

`PRD.md` · `BUILD_PLAN.md` · `DEMO_SCRIPT.md`

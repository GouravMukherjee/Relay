# Relay — Build Plan & Task Breakdown

**Status:** Draft for build · **Owner:** Product/Pitch (coordination) · **Last updated:** June 2026
**Window:** 24 hours · **Roles:** BE (backend/systems), FE (frontend/demo), PP (product/pitch), FL (floater/integrations)

---

## Phase plan

| Phase | Hours | Goal | Exit criteria |
|-------|-------|------|---------------|
| 0 — Scope lock | 0–3 | No code. Freeze contracts + demo story. Pitch idea to sponsor reps. | API_SPEC + DATA_MODEL + DEMO_SCRIPT agreed. Repo + skeleton up. |
| 1 — Core pipeline | 3–12 | Relay Live end-to-end in ugliest form. | Speaking into the mic surfaces a grounded card on screen. |
| 2 — Layer + polish | 12–18 | Desk + Intake as thin configs; card UI polish; memory. | 2nd mode demoable; cards look clean. |
| 3 — Demo hardening | 18–22 | Bulletproof the scripted flow. | ≥9/10 dry runs succeed; recorded backup exists. |
| 4 — Pitch + launch | 22–24 | 2-min pitch tight; landing page + waitlist live. | Pitch rehearsed; page collecting emails. |

---

## Tasks

### Phase 0 — Scope lock
- **T0.1 (PP)** Finalize demo story + dataset list → DEMO_SCRIPT.md.
- **T0.2 (BE)** Freeze API_SPEC.md + DATA_MODEL.md. Stand up repo, CLAUDE.md, `.claude/agents/`.
- **T0.3 (FL)** Collect sponsor keys (Moss, LiveKit, Unsiloed) into `.env`; verify each pings.
- **T0.4 (PP)** Talk to Moss + LiveKit reps on the floor; note feedback.

### Phase 1 — Core pipeline (critical path)
- **T1.1 (BE)** LiveKit room + agent worker joins, subscribes to audio. *(blocks T1.2)*
- **T1.2 (BE)** Streaming STT → partial/final transcripts. *(blocks T1.3, T1.6)*
- **T1.3 (BE)** Trigger detector (question + debounced continuous).
- **T1.4 (FL)** Ingestion: Unsiloed parse → chunk → embed → Moss index + Postgres.
- **T1.5 (BE)** Retrieval Service → Moss top-k (+ pgvector fallback). *(needs T1.4)*
- **T1.6 (BE)** Orchestrator → Claude grounded card (answer + source). *(needs T1.5)*
- **T1.7 (FE)** WebSocket client + live transcript + card render. *(needs API_SPEC)*
- **T1.8 (FL)** Seed the demo dataset through ingestion; verify query-ready.
- **🎯 Milestone M1:** mic → card on screen, grounded + cited, <500 ms perceived.

### Phase 2 — Layer + polish
- **T2.1 (BE)** Desk mode: reactive `/query` over docs + customer memory.
- **T2.2 (BE)** Intake mode: qualifying flow → ICP score → lead record + route.
- **T2.3 (BE)** Memory service: store/retrieve session summaries + facts.
- **T2.4 (FE)** Mode switcher; card pin/dismiss/expand-source; latency badge.
- **T2.5 (FE)** Visual polish pass (matches DEMO_SCRIPT staging).
- **🎯 Milestone M2:** two modes demoable; cards clean.

### Phase 3 — Demo hardening
- **T3.1 (All)** Run the exact demo on the exact dataset, 10×; log failures.
- **T3.2 (BE)** Pre-warm Claude conn; pre-cache demo queries; pin retrieval backend.
- **T3.3 (FE)** Record a clean screen capture of the full working flow (backup).
- **🎯 Milestone M3:** scripted flow reliable; backup recorded.

### Phase 4 — Pitch + launch
- **T4.1 (PP)** Tighten 2-min pitch to DEMO_SCRIPT beats.
- **T4.2 (FE/FL)** One-page landing site + email waitlist.
- **T4.3 (PP)** Submit to all eligible tracks (Co-Pilot primary; Desk→Support; Intake→Lead Gen).

---

## Critical path

`T1.1 → T1.2 → T1.3 → (T1.4 → T1.5) → T1.6 → T1.7 → M1`

If behind at hour 12: **cut Phase 2 to one mode**, protect M1 + M3. A flawless single-mode demo beats three broken ones.

## Dependencies on contracts

FE (T1.7) and BE (T1.6) both consume API_SPEC.md — it must be frozen in Phase 0 or the halves won't connect.

## Related docs

`SCOPE.md` · `DEMO_SCRIPT.md` · `TEST_PLAN.md` · `API_SPEC.md`

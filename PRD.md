# Relay — Product Requirements Document (PRD)

**Status:** Draft for build · **Owner:** Product/Pitch · **Last updated:** June 2026
**Event:** YC × Moss Conversational AI Hackathon (June 6–7, 2026)

---

## 1. Summary

Relay is an **ambient AI co-pilot** that listens to a live conversation and surfaces the right answer from your team's own knowledge base — as a card on screen, in under half a second. It is grounded retrieval, not a generic chatbot: every answer is pulled from *your* documents and cited.

One engine powers three modes, each mapping to a hackathon track:

| Mode | Track | Behaviour |
|------|-------|-----------|
| **Relay Live** | Co-Pilot | Listens to a live call, surfaces context cards, never interrupts. *(Hero demo.)* |
| **Relay Desk** | Support | Reactive: pulls the right doc + customer history to resolve a question. |
| **Relay Intake** | Lead Gen | Qualifies an inbound lead in conversation, scores, and routes hot ones. |

## 2. Problem

Knowledge workers spend the majority of their day *not* doing their core job — they're hunting for information. Sellers spend only ~25% of their time selling; ~64% goes to non-selling work. On live calls, the cost is acute: a question comes up, the answer exists in a doc somewhere, but finding it takes 30 seconds the conversation doesn't have. Existing live-assist tools (e.g. Cluely-class) generate plausible answers from a general model — they have no access to the company's real knowledge, so they're confident but unreliable.

## 3. Target users

- **Primary:** Customer-facing teams at 10–50 person companies (sales, CS, solutions) who live on calls and own a sprawling, under-organized knowledge base.
- **Secondary (modes):** Support agents (Desk), inbound SDRs / founders fielding leads (Intake).
- **Buyer:** Founder / RevOps / Head of CS at an SMB who feels the "nobody can find anything" pain.

## 4. Goals & non-goals

**Goals**
- Surface a *grounded, cited* answer during a live conversation in <500 ms perceived latency.
- Make ingestion trivial: drop in PDFs/docs, be query-ready in minutes.
- One codebase, three demoable modes.

**Non-goals (for the hackathon)**
- Enterprise auth/SSO, multi-tenant security hardening, billing.
- Mobile apps. Native meeting-platform plugins (Zoom/Meet) beyond a basic path.
- Long-horizon analytics dashboards.

## 5. User stories

- *As a salesperson on a live call,* when a prospect raises an objection, I want the rebuttal + supporting proof to appear on screen instantly so I never stall.
- *As a CS agent,* when a customer describes a recurring issue, I want their history + the exact fix doc surfaced so I don't make them repeat themselves.
- *As a founder fielding an inbound call,* I want the agent to ask the right qualifying questions, score the lead, and ping me if it's hot.
- *As an admin,* I want to upload our docs once and trust that answers are pulled only from them, with a citation I can verify.

## 6. Core user flows

**Relay Live**
1. User joins a call; Relay joins the audio room (LiveKit).
2. Speech is transcribed in real time; a trigger detector spots questions/topics.
3. The query hits Moss retrieval (<10 ms) → top-k chunks.
4. The orchestrator (Claude) composes a concise card: answer + source.
5. Card streams to the dashboard over WebSocket and renders beside the call.

**Relay Desk** — same pipe, reactive: a user message → retrieval over docs + that customer's memory → resolution card.

**Relay Intake** — same pipe, proactive: a scripted-but-natural qualifying flow → ICP scoring → route + notify.

## 7. Success criteria

**Demo (hackathon)**
- Live Mode runs end-to-end on the demo dataset with a card appearing in <500 ms perceived.
- At least two modes demoable.
- A working ingestion path (upload → query-ready).

**Product (post-hackathon signal)**
- A landing page with an email waitlist live within 24h of winning.
- ≥20 qualified signups in the week before the YC interview.

## 8. Key metrics

- **Time-to-card** (trigger → rendered): target <500 ms perceived.
- **Retrieval latency** (Moss query): target <10 ms.
- **Grounding rate:** % of cards with a valid source citation (target 100%).
- **Demo reliability:** scripted flow succeeds on ≥9/10 dry runs.

## 9. Open questions

- Trigger model: fire continuously, on detected questions, or hybrid? (See TDD §Trigger.)
- Whisper-back (TTS to an earpiece) vs on-screen cards only for the demo? (Default: cards.)
- How much cross-session memory to wire for the demo vs stub? (See SCOPE.md.)

## 10. Related docs

`TECHNICAL_DESIGN.md` · `API_SPEC.md` · `DATA_MODEL.md` · `SCOPE.md` · `BUILD_PLAN.md` · `DEMO_SCRIPT.md` · `ADRs.md` · `TEST_PLAN.md`

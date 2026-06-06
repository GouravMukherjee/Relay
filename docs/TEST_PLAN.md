# Relay — Test Plan

**Status:** Draft for build · **Owner:** Systems/Backend + Floater · **Last updated:** June 2026

Lightweight by design. For a 24h build, testing exists to **protect the demo-critical path**, not to chase coverage. Priorities: (1) the four Live beats work, (2) latency stays in budget, (3) nothing hallucinates, (4) fallbacks engage cleanly.

---

## 1. Demo-critical path (highest priority)

| ID | Test | Pass condition |
|----|------|----------------|
| D1 | Each of the 4 Live beats (DEMO_SCRIPT) returns the correct card | Correct doc cited, 10/10 runs |
| D2 | Mode switch Live → Desk → Intake | No crash, state preserved, <1 s |
| D3 | Desk recall on Acme history | Card references prior ticket + correct fix |
| D4 | Intake qualifying flow | Lead scored, status set, routed message emitted |
| D5 | Backup recording plays | Full flow captured and cued |

## 2. Latency (the moat)

| ID | Test | Target |
|----|------|--------|
| L1 | Moss query latency (p50/p95) | <10 ms / <25 ms |
| L2 | Trigger → card rendered (perceived) | <500 ms |
| L3 | Claude synthesis (short card) | <300 ms |
| L4 | Cold start of first card after session open | <800 ms (pre-warm to hit this) |

Measure via the `latency_ms` field on each card + server timing logs. Run under demo conditions (local pre-indexed dataset).

## 3. Grounding & safety (trust)

| ID | Test | Pass condition |
|----|------|----------------|
| G1 | Question with no supporting doc | Returns "no card" — never invents an answer |
| G2 | Every card has ≥1 valid source | 100% of cards cite a real chunk |
| G3 | Citation resolves to real text | Snippet matches the source chunk |
| G4 | Out-of-scope question (off-topic) | No card or graceful "not in knowledge base" |

## 4. Ingestion

| ID | Test | Pass condition |
|----|------|----------------|
| I1 | Upload each demo doc | status → ready; chunk_count > 0 |
| I2 | PDF with a table (Pricing) | Table facts retrievable (Unsiloed parse OK) |
| I3 | Re-ingest same doc | Idempotent; no duplicate chunks |
| I4 | Unsupported file type | Clean `document_unsupported` error |

## 5. Fallbacks & failure modes

| ID | Test | Pass condition |
|----|------|----------------|
| F1 | Moss disabled | pgvector fallback serves grounded cards |
| F2 | STT drops/degrades | Partials shown; manual typed query works |
| F3 | WebSocket disconnect | Auto-reconnect; session resumes |
| F4 | LLM timeout | Graceful "retrieving…" then retry, no crash |

## 6. Smoke test (run before every dry run)

```
[ ] DB up, migrations applied
[ ] All demo docs status=ready (I1)
[ ] One Live beat returns correct card (D1 sample)
[ ] Latency badge <500 ms (L2)
[ ] Mode switch works (D2)
[ ] Backup recording cued (D5)
```

## 7. What we are NOT testing (hackathon)

Load/concurrency, auth, multi-tenant isolation, browser matrix, accessibility, billing. Noted for post-hackathon hardening.

## Related docs

`DEMO_SCRIPT.md` · `SCOPE.md` · `TECHNICAL_DESIGN.md`

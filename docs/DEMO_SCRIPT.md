# Relay — Demo Script & Dataset

**Status:** Draft for build · **Owner:** Product/Pitch + Frontend · **Last updated:** June 2026

Hackathons are won on the demo. This is the exact flow we rehearse and the exact data it runs on. Two minutes, no dead air, one "wow" moment in the first 30 seconds.

---

## The demo dataset — "Northwind" (fictional SaaS company)

We ingest a small, believable knowledge base for a fake company so every answer is grounded and verifiable on screen.

| Doc | Contents (key facts the demo will hit) |
|-----|----------------------------------------|
| `Northwind_MSA.pdf` | Uptime SLA **99.9%**; data deletion within 30 days; liability cap. |
| `Northwind_Security.pdf` | **SOC 2 Type II**, encryption at rest/in transit, SSO on Enterprise plan. |
| `Northwind_Pricing.pdf` | Starter $49/seat, Growth $99/seat, Enterprise custom; annual discount 15%. |
| `Northwind_Battlecard.pdf` | vs "Acme": Relay wins on real-time + grounding; Acme is post-call only. |
| `Northwind_FAQ.pdf` | Onboarding time, integrations list, support hours. |
| `Customer_Acme_history.txt` | (Desk) Prior ticket: CRM export sync issue, fixed via re-auth on Growth tier. |

> All facts are fictional. The point is that the card quotes *this* doc, visibly.

---

## Relay Live — primary demo (the hero, ~75s)

**Setup on screen:** split view — left: "live call" mic indicator + transcript; right: Relay cards.

| Beat | Spoken (presenter as "prospect") | Expected card (<500 ms) |
|------|----------------------------------|--------------------------|
| 1 | "Before we go further — what's your uptime guarantee?" | **99.9% uptime SLA** · source: MSA |
| 2 | "And are you SOC 2 compliant?" | **SOC 2 Type II**, encryption at rest & in transit · source: Security |
| 3 | "Honestly, Acme told us they do the same thing for less." | **Battlecard:** Acme is post-call only; Relay is real-time + grounded · source: Battlecard |
| 4 | "What would Enterprise run us?" | **Enterprise = custom; Growth $99/seat, 15% annual discount** · source: Pricing |

**Narration over the top:** "Notice — every answer is pulled from Northwind's actual documents, cited, in under half a second. The model isn't guessing. It's retrieving."

## Relay Desk — second beat (~25s)

Switch mode. Type/speak as a returning customer:
- "Hey, that sync issue is back." → Card: *"You hit this before — CRM export sync, fixed by re-auth on your Growth tier. Here's the step."* · sources: FAQ + Acme history.
- **Line:** "Same engine. Now it remembers the customer."

## Relay Intake — optional third beat (~20s)

Switch mode. Inbound caller:
- Agent asks 2 qualifying questions → fills budget/timeline/need → score **82 (hot)** → "Routed to #sales."
- **Line:** "Same engine again — now qualifying and routing a lead live."

## Close (~15s)

> "One ambient retrieval engine. Three agents — Co-Pilot, Support, Lead Gen. Built on Moss for sub-10ms grounding, LiveKit for live audio, Unsiloed for the docs. We're not adding a feature. We're replacing the scramble for information."

---

## Staging & safety

- Run on the **local pre-indexed dataset**; do not depend on conference Wi-Fi for retrieval.
- **Pre-cache** the four Live queries; pin retrieval backend to Moss (fallback ready).
- Keep a **recorded screen capture** of the full flow cued up as backup.
- Presenter speaks the beats verbatim; second person watches cards and narrates.
- If a card misfires: use the typed manual-query fallback without breaking stride.

## Dry-run checklist

- [ ] All six docs ingested, status=ready.
- [ ] Each Live beat returns the right card 10× in a row.
- [ ] Latency badge shows <500 ms on each.
- [ ] Mode switch Live→Desk→Intake is smooth.
- [ ] Backup recording cued.

## Related docs

`PRD.md` · `SCOPE.md` · `TEST_PLAN.md`

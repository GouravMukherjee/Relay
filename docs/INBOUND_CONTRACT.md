# Inbound channel contract (Northwind customer → Desk/Intake → reply)

This is the **frozen contract** for the customer-facing inbound channel built for the demo.
Every slice (backend, customer site, rep dashboard) implements against THIS doc so the
parallel work composes. Brand: **Northwind** (the tenant company the demo is "for").

## Mental model

A **thread** is one customer conversation. It maps deterministically to a Relay **session**
so the rep's existing Desk/Intake panels (which already speak the `session_id`-keyed WS
event language) light up with zero new plumbing:

```
thread_id  ──(stable map)──▶  session_id = stable_session_id("inbound:" + thread_id)
org_id     = settings.inbound_org_id  or  settings.default_org_id
```

There are **two sockets**, both already fanned out cross-process by the existing `WsHub`
(Redis-backed):

- **Rep socket** — the dashboard's existing `WS /ws/sessions/{session_id}` (Supabase-auth).
  Receives `transcript.final`, `card.new`/`card.update`, `lead.update`, `session.status`.
- **Widget socket** — a NEW **public** `WS /ws/inbound/{thread_id}` (no auth). Receives
  `message` and `status` events. The customer site connects here.

The customer message and the agent reply are persisted as `Utterance` rows on the session
(speaker `"customer"` / `"agent"`) so Transcripts + history stay consistent.

## Public REST (mounted under `/api/v1`, NO Supabase auth)

### `POST /inbound/threads`
Create or resolve a thread. Body: `{ "display_name"?: string }`.
Response `200`:
```json
{ "thread_id": "northwind-support", "ws_url": "/ws/inbound/northwind-support" }
```
For the demo, the default thread is `settings.inbound_demo_thread`. A fresh random thread
id is also acceptable if `display_name` implies a new visitor.

### `POST /inbound/threads/{thread_id}/messages`
Customer sends a message. Body: `{ "text": string, "display_name"?: string }`.
Response `202`: `{ "status": "received" }`.

**Server pipeline on a customer message** (all best-effort, never 500 the widget):
1. Resolve `session_id` + `org_id` (see map above). Ensure the `Session` row exists.
2. Persist `Utterance(speaker="customer", text)`.
3. **Echo to widget**: `WS /ws/inbound/{thread_id}` → `{type:"message", data:{role:"customer", text, ts}}`.
4. **Notify rep**: `WS /ws/sessions/{session_id}` → `transcript.final {utterance_id, speaker:"customer", text}`.
5. **Classify intent** (LLM, support vs sales) → broadcast routing to BOTH sockets:
   - rep: `session.status {status:"active", retrieval_backend, routing:{department:"desk"|"intake", confidence}}`
   - widget: `{type:"status", data:{routed_to:"desk"|"intake", agent_typing:true}}`
6. **Intake triage ALWAYS runs in parallel** (`extract_and_store`) → `lead.update` to rep.
   The lead name updates live: starts `"Unknown caller"`, becomes the real name the moment
   it appears in the transcript (re-broadcast on change).
7. **If support** → run Desk grounded synthesis (the streamed `emit` path) → `card.new` /
   `card.update` to rep (the suggested reply). Desk answers stay grounded + cited.
8. Set `agent_typing:false` on the widget when synthesis completes.

### Rep reply → back to the widget
The existing `POST /sessions/{session_id}/reply` (Supabase-auth) is extended: in addition
to logging, it now **delivers to the widget** when the session is an inbound thread:
- Persist `Utterance(speaker="agent", text)`.
- `WS /ws/inbound/{thread_id}` → `{type:"message", data:{role:"agent", text, ts}}`.

`reverse map`: `thread_id` is recoverable from `session_id` (keep a small in-process map, or
re-derive: the session's `livekit_room`/metadata stores the thread id). Simplest: store
`thread_id` on the session row's `livekit_room` field as `"inbound:" + thread_id`, or keep a
module dict `session_id -> thread_id`.

## WebSocket events

### Widget socket `/ws/inbound/{thread_id}` (public) — server → client
```jsonc
{ "type": "message", "ts": "ISO", "data": { "role": "customer" | "agent", "text": string } }
{ "type": "status",  "ts": "ISO", "data": { "routed_to": "desk" | "intake", "agent_typing": boolean } }
```
The widget MAY also send messages over this socket instead of the REST POST:
`{ "type": "message", "data": { "text": string } }` — treat identically to the REST message.

### Rep socket `/ws/sessions/{session_id}` — additions (additive, back-compatible)
- `session.status.data.routing = { department: "desk" | "intake", confidence: number }` (optional field).
- Everything else (`transcript.final`, `card.new`, `card.update`, `lead.update`) is unchanged.

## Routing / triage semantics

- **Intake = live triage.** Every inbound runs Intake qualification regardless of intent, so
  the lead/qualifiers populate and the department is decided in parallel.
- **Classifier** returns `support` (→ Desk grounded reply) or `sales` (→ Intake lead focus).
  Ambiguous/unclear → default `support` (answer the question) but keep Intake populated.
- The rep UI shows a **routing badge** (which department) and both panels may carry data.

## Rep dashboard wiring (frontend)

- Desk and Intake, when active, watch the **inbound session** (`GET /inbound/session` returns
  `{ session_id, ws_url, thread_id }` — analogous to `/sessions/demo`) so customer messages,
  cards, and leads stream in with no manual room selection.
- **Desk**: render incoming `customer` utterances in the conversation; the streamed suggested
  reply appears in the RESOLUTION panel; **Send reply** posts to `/sessions/{id}/reply` →
  reaches the widget.
- **Intake**: the LEAD card shows `"Unknown caller"` until a name arrives, then updates
  instantly; the routing badge shows the classified department.

### `GET /inbound/session` (Supabase-auth, rep side)
Returns the rep's view of the demo inbound thread:
```json
{ "session_id": "ses_…", "ws_url": "/ws/sessions/ses_…", "thread_id": "northwind-support" }
```

## Northwind customer site (frontend, standalone)

A small, production-looking branded support page for **Northwind** with a chat widget:
- Calls `POST /inbound/threads` once to get `thread_id` + `ws_url`, connects the widget WS.
- Sends messages (REST POST or over the WS), renders the `customer`/`agent` message stream,
  shows a subtle "routed to {dept}" / "agent is typing…" indicator from `status` events.
- Lives under its own path/app so it's clearly the *customer's* view, not the rep console.

## Non-goals (demo scope)

- No real email/SMS channel — the widget IS the channel.
- Widget is unauthenticated (public). Fine for a local demo; gate behind a token before prod.
- One active demo thread is enough; multi-visitor is a nice-to-have, not required.

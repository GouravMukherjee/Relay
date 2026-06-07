# Inbound phone calls (LiveKit SIP) — Relay Live mode

Relay's **Live** mode accepts a real inbound phone call and surfaces grounded, cited
cards on the rep dashboard — the same STT → trigger → Moss → card pipeline used for the
browser mic. A SIP caller is just another audio participant; the transcription logic is
**not** special-cased.

## How it fits together

```
  PSTN / SIP caller ──▶ LiveKit SIP trunk ──▶ room "relay-demo"
                                                   │  (dispatch rule → relay-agent)
                                                   ▼
                               relay-agent (named agent, explicit dispatch)
                                 • STT (LiveKit Inference) on the caller's audio
                                 • TriggerDetector → Orchestrator (Moss + Haiku, streamed)
                                 • broadcasts card.new / card.update over the WsHub
                                                   │  (session_id = stable_session_id("relay-demo"))
                                                   ▼
                               Rep dashboard, Live view (Phone source = default)
                                 • watches /ws/sessions/<demo session id>
                                 • "Incoming call" indicator + live timer on SIP join
```

Key invariants:

- The agent worker is a **named agent** (`LIVEKIT_AGENT_NAME=relay-agent`,
  `WorkerOptions.agent_name`). With a name set it runs **only on explicit dispatch** —
  the gateway dispatches it to each browser-session room, and the SIP dispatch rule
  dispatches it to `relay-demo` for inbound calls.
- The demo room's session id is **deterministic** (`stable_session_id("relay-demo")`), so
  the agent and the dashboard agree on the WS channel without coordination.
- The card latency path (Haiku fast model + token streaming + low `max_tokens`) applies
  unchanged on the phone path — the first token paints in well under the 3 s budget.

## Env

```dotenv
LIVEKIT_URL=wss://<project>.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
LIVEKIT_AGENT_NAME=relay-agent      # explicit-dispatch name
LIVEKIT_DEMO_ROOM=relay-demo        # fixed inbound-phone room
LIVEKIT_STT_MODEL=assemblyai/universal-streaming
```

## One-time SIP setup (LiveKit CLI)

Use `lk` (never hand-write keys). Create an inbound trunk + a dispatch rule that drops
callers into the demo room **and dispatches `relay-agent`**:

```bash
# 1) Inbound SIP trunk (returns a trunk id; numbers/providers per your LiveKit project)
lk sip inbound create inbound-trunk.json

# 2) Dispatch rule: route inbound calls into relay-demo, dispatching relay-agent.
lk sip dispatch create dispatch-rule.json
```

`dispatch-rule.json` (fixed room + explicit agent dispatch):

```json
{
  "name": "relay-demo-inbound",
  "trunk_ids": ["<inbound-trunk-id>"],
  "rule": { "dispatchRuleDirect": { "roomName": "relay-demo" } },
  "roomConfig": {
    "agents": [{ "agentName": "relay-agent", "metadata": "{\"mode\":\"live\"}" }]
  }
}
```

> The agent reads dispatch metadata from `ctx.job.metadata`; for the demo room it falls
> back to the deterministic demo session id and the default org when metadata is absent,
> so even a bare SIP dispatch works.

## Verify (no phone required)

1. Start the stack: `make run-gateway`, `make run-agent` (the agent registers as
   `relay-agent`), and the frontend.
2. Dispatch the agent to the demo room:
   ```bash
   cd backend && python -m relay.agent.dispatch
   # equivalent: lk dispatch create --agent-name relay-agent --room relay-demo
   ```
3. Open the dashboard → **Live** (the **Phone** source is the default; it watches
   `relay-demo`).
4. Join `relay-demo` as a participant and speak a question (a softphone/SIP call, the
   `lk room join` mic, or flip the dashboard's source toggle to **Mic**). When a SIP
   participant joins, the dashboard shows the **Incoming call** indicator and the live
   timer; a question like *"What's your uptime SLA?"* streams a cited card in < 3 s.

## Browser-mic fallback (unchanged)

The original browser-mic flow is preserved as a fallback: the **Mic** toggle in the Live
header creates a normal per-session room, publishes the device microphone, and dispatches
`relay-agent` to that room. Mute/un-mute toggles the existing track (no reconnect).

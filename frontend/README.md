# Relay — Frontend (Card UI)

The React + TypeScript dashboard for Relay: live transcript on the left, grounded
cited cards on the right, with a Live / Desk / Intake mode switcher. Built to the
frozen contract in [`../API_SPEC.md`](../API_SPEC.md).

## Run it

```bash
npm install
npm run dev          # http://localhost:5173
```

It ships **demo-ready**: with no backend, an in-browser engine replays the exact
[`../DEMO_SCRIPT.md`](../DEMO_SCRIPT.md) beats — streaming transcripts, sub-500 ms
grounded cards, mode switching, and a scripted Intake lead.

- **Demo controls:** click **Beat** (next to the input) to advance the rehearsed
  script one line at a time, or type any question into the manual-query bar.
- The four Live beats (uptime SLA, SOC 2, Acme battlecard, Enterprise pricing),
  the Desk sync-issue recall, and the Intake lead all resolve against the
  bundled Northwind knowledge base.

## Demo ↔ Functional toggle

One variable flips the whole app between the in-browser demo and a real backend
(see [`.env.example`](.env.example)):

```bash
VITE_DEMO_MODE=true                       # demo: mock engine, no network (default)
VITE_DEMO_MODE=false                      # functional: connect to the backend
VITE_BACKEND_URL=http://192.168.1.50:8000 # the backend IP/origin
```

`VITE_BACKEND_URL` accepts a bare `IP[:port]` or a full URL; a bare host is
normalized to `http://`. In **functional** mode the frontend talks to that origin
**directly** — REST at `<backend>/api/v1`, WebSocket at `<backend>/ws` — so the
backend must allow CORS from the frontend origin.

When functional, the app:

1. `POST <backend>/api/v1/sessions` to create a session,
2. opens the returned `ws_url` WebSocket (resolved against `VITE_BACKEND_URL`) for
   `transcript.*` / `card.*` / `session.status` events — with auto-reconnect and a
   connection indicator in the sidebar,
3. sends `mode.set`, `query.manual`, `card.pin`, `card.dismiss` upstream,
4. surfaces unreachable-backend errors as a toast instead of a blank screen.

**CORS-free local dev:** alternatively set `VITE_API_BASE=/api/v1` and the Vite
dev server proxies `/api` + `/ws` to `VITE_BACKEND_URL` (same-origin, no CORS).

> `VITE_USE_MOCK` is still honored as a back-compat alias for `VITE_DEMO_MODE`.

## Backend wiring — every control points at the gateway

All buttons and tabs call the gateway through `api/client.ts` (REST) or the
WebSocket transport. With no backend (`VITE_USE_MOCK=true`) each action shows a
toast naming the endpoint it *would* hit ("… → POST /documents · pending setup");
flip `VITE_USE_MOCK=false` and the same handlers fire real requests — no code
change needed.

| Control | Backend call |
|---------|--------------|
| Live / Desk / Intake tabs | WS `mode.set` (+ session per mode) |
| Sidebar → New Analysis | `POST /sessions` |
| Sidebar → Transcripts / Knowledge / Team | `GET /sessions` · `GET /documents` · `GET /users` |
| Top nav → 🔔 / ⚙ / avatar | `GET /notifications` · `GET /me` |
| Live → mic | `POST /sessions/{id}/livekit-token` |
| Live/Desk → drop / attach document | `POST /documents` |
| Composer send (Live/Desk) | WS `query.manual` |
| Desk → Send reply / Edit | `POST /sessions/{id}/reply` |
| Knowledge → Upload / delete row | `POST /documents` · `DELETE /documents/{id}` |
| Intake → Route to #sales / Book meeting | `POST /leads/{id}/route` · `POST /leads/{id}/book` |
| Card copy | clipboard (local) |

Endpoints beyond the frozen `API_SPEC.md` (`/me`, `/users`, `/notifications`,
`/sessions/{id}/livekit-token`, `/sessions/{id}/reply`, `/leads/{id}/book`) are
marked **additive** in `api/client.ts` and follow the same conventions — the
gateway is expected to implement them.

## Layout

```
src/
  types.ts              # API_SPEC object shapes + WS event envelopes
  config.ts             # mock toggle, API base, ws url helper
  util.ts               # clock() + initials() helpers
  backend.tsx           # ToastProvider + useBackend().call() (gateway-or-toast)
  api/client.ts         # REST client (spec + additive endpoints)
  api/transport.ts      # RelayTransport interface + real WebSocket impl
  mock/dataset.ts       # Northwind KB + scripted demo beats + demo chrome data
  mock/engine.ts        # in-browser RelayTransport (demo engine)
  hooks/useRelaySession # session lifecycle + reactive state + actions
  hooks/useResource     # fetch-or-demo for the section tabs
  components/            # TopNav, Sidebar, Waveform, RelayCard, Icon
  views/                # Live/Desk/Intake + Knowledge/Transcripts/Team
  styles/global.css     # the design system (see below)
```

## Design

Implements the **Stitch "Functional White" design system** from
`../stitch_relay_ai_co_pilot_dashboard/relay_design_system/DESIGN.md` — Modern
Corporate Minimalism for high-stakes, real-time B2B:

- **Geist** type (+ Geist Mono for timestamps, IDs, latency), **Material Symbols**
  icons.
- Indigo **#4F46E5** primary for actions/active states; emerald **#10B981** for
  "Live" pulse and *Verified* badges; a functional-white surface stack with 1px
  hairline borders over shadows.
- **Shell:** top nav (brand · Live/Desk/Intake switcher · actions) + a 256px nav
  sidebar + a 38/62 split main area, capped at 1440px on a 4px spacing grid.
- **Per-mode layouts** mirror the Stitch screens: Live (call + suggested answers),
  Desk (conversation + customer/resolution), Intake (inbound call + lead/qualification).

The latency badge counts up to its value on each card to make the "<500 ms"
claim visible.

### Motion

All animation is **Framer Motion**, driven by a shared vocabulary in `src/motion.ts`
(easing, spring presets, reveal variants, hover/press helpers):

- **Scroll-triggered fades** — `whileInView` + `viewport={inView}` (fire once) on
  the qualification card, document table, and the Transcripts / Team grids.
- **Staggered reveals** — `staggerParent` containers cascade their children:
  sidebar nav, transcript lines, chat bubbles, qualifier rows, table rows, grid cards.
- **Smooth hover/press** — `hoverCard` / `pressable` / `iconHover` on cards, buttons,
  nav links, tabs, and icon controls; answer cards lift on hover.
- **Presence** — `AnimatePresence` for card/transcript enter-exit, toast in/out,
  expanding source snippets, and a cross-fade between dashboard ↔ section views.

Continuous ambient indicators (the emerald live-pulse, the audio waveform) stay as
lightweight CSS/rAF — Framer Motion owns every reveal, transition, and interaction.

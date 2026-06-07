// Runtime configuration & the demo↔functional toggle.
//
//   VITE_DEMO_MODE   "true"  → demo state: in-browser mock engine, no network.
//                    "false" → functional site: connect to the real backend.
//   VITE_BACKEND_URL  the backend IP/origin, e.g. "192.168.1.50:8000" or
//                     "https://api.example.com". A bare host is normalized to http://.
//
// CORS strategy (default): REST goes to a SAME-ORIGIN relative path (/api/v1) that a
// proxy forwards to the backend — the Vite dev server locally, the vercel.json rewrite
// in production. Same-origin means no CORS preflight, so it "just works" with no
// backend change. WebSockets can't be proxied that way, so they connect directly to
// VITE_BACKEND_URL (a WS handshake is not subject to CORS).
//
// To talk to the backend DIRECTLY instead (e.g. you've enabled CORS server-side),
// set VITE_API_BASE to an absolute URL like https://api.example.com/api/v1.

const env = import.meta.env;

function normalizeOrigin(v: string): string {
  let s = v.trim().replace(/\/+$/, "");
  if (!/^https?:\/\//i.test(s)) s = `http://${s}`; // allow a bare IP or host:port
  return s;
}

// Backend origin (scheme + host + port), e.g. http://192.168.1.50:8000
export const BACKEND_URL: string = normalizeOrigin(
  env.VITE_BACKEND_URL ?? env.VITE_GATEWAY_URL ?? "http://localhost:8000",
);

// Just the host:port, for compact display in the UI.
export const BACKEND_HOST: string = BACKEND_URL.replace(/^https?:\/\//i, "");

// LiveKit Cloud URL the browser uses to join the room + publish the mic in
// functional mode (e.g. wss://<project>.livekit.cloud). Empty = no live audio
// (manual typed queries still work).
export const LIVEKIT_URL: string = (env.VITE_LIVEKIT_URL ?? "").trim();

// The toggle. VITE_DEMO_MODE is canonical; VITE_USE_MOCK kept as a back-compat alias.
const demoRaw = env.VITE_DEMO_MODE ?? env.VITE_USE_MOCK ?? "true";
export const DEMO_MODE: boolean = String(demoRaw).toLowerCase() !== "false";

// Back-compat alias used throughout the app.
export const USE_MOCK = DEMO_MODE;

// REST base. Same-origin relative path by default (proxied → no CORS). Override
// with an absolute VITE_API_BASE to hit the backend directly (needs backend CORS).
export const API_BASE: string = env.VITE_API_BASE ?? "/api/v1";

// Build a WebSocket URL. Accepts an absolute ws(s):// URL or a gateway path like
// "/ws/sessions/ses_…". WS always connects DIRECTLY to the backend origin
// (VITE_WS_BASE, else VITE_BACKEND_URL) — it can't be proxied through Vercel, and a
// WS handshake isn't subject to CORS.
export function wsUrl(path: string): string {
  if (/^wss?:\/\//i.test(path)) return path;
  if (env.VITE_WS_BASE) return `${env.VITE_WS_BASE}${path}`;
  const wsOrigin = BACKEND_URL.replace(/^http/i, "ws");
  return `${wsOrigin}${path.startsWith("/") ? "" : "/"}${path}`;
}

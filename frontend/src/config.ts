// Runtime configuration & the demo↔functional toggle.
//
//   VITE_DEMO_MODE   "true"  → demo state: in-browser mock engine, no network.
//                    "false" → functional site: connect to the real backend.
//   VITE_BACKEND_URL  the backend IP/origin, e.g. "192.168.1.50:8000" or
//                     "http://192.168.1.50:8000". A bare host is normalized to http://.
//
// In functional mode the frontend talks to the backend DIRECTLY at VITE_BACKEND_URL
// (REST under /api/v1, WebSocket under /ws). The backend must allow CORS from the
// frontend origin. For CORS-free local dev you can instead set VITE_API_BASE=/api/v1
// and let the Vite dev proxy forward to VITE_BACKEND_URL (see vite.config.ts).

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

// The toggle. VITE_DEMO_MODE is canonical; VITE_USE_MOCK kept as a back-compat alias.
const demoRaw = env.VITE_DEMO_MODE ?? env.VITE_USE_MOCK ?? "true";
export const DEMO_MODE: boolean = String(demoRaw).toLowerCase() !== "false";

// Back-compat alias used throughout the app.
export const USE_MOCK = DEMO_MODE;

// REST base. Absolute (direct to backend) unless overridden with a relative path
// to go through the dev proxy.
export const API_BASE: string = env.VITE_API_BASE ?? `${BACKEND_URL}/api/v1`;

// Build a WebSocket URL. Accepts an absolute ws(s):// URL or a gateway path like
// "/ws/sessions/ses_…" (which is resolved against the backend origin).
export function wsUrl(path: string): string {
  if (/^wss?:\/\//i.test(path)) return path;
  if (env.VITE_WS_BASE) return `${env.VITE_WS_BASE}${path}`;
  // If REST is proxied (relative API_BASE), proxy the WS through the dev server too.
  if (API_BASE.startsWith("/")) {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${window.location.host}${path}`;
  }
  const wsOrigin = BACKEND_URL.replace(/^http/i, "ws");
  return `${wsOrigin}${path.startsWith("/") ? "" : "/"}${path}`;
}

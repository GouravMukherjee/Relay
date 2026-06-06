// Runtime config. The frontend ships demo-ready: with no backend it runs against
// an in-browser mock engine that replays the DEMO_SCRIPT.md beats. Point it at a
// real gateway by setting VITE_USE_MOCK=false (and configuring the proxy / URLs).

const env = import.meta.env;

export const USE_MOCK: boolean =
  (env.VITE_USE_MOCK ?? "true").toString().toLowerCase() !== "false";

// Same-origin by default; Vite proxies /api and /ws to the gateway in dev.
export const API_BASE: string = env.VITE_API_BASE ?? "/api/v1";

export function wsUrl(path: string): string {
  if (env.VITE_WS_BASE) return `${env.VITE_WS_BASE}${path}`;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}${path}`;
}

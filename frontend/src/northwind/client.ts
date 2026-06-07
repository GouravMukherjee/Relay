// Tiny standalone fetch + WebSocket client for the Northwind customer widget.
// Talks ONLY to the public inbound channel (docs/INBOUND_CONTRACT.md). No auth.
//
// Send-path decision: we send customer messages over the WebSocket
//   { type: "message", data: { text } }
// which the contract says is treated identically to the REST POST. We fall back
// to the REST POST /inbound/threads/{id}/messages only if the socket isn't open
// (e.g. mid-reconnect) so a message is never silently dropped.

import type { CreateThreadResponse, WsInboundEvent } from "./types";

// The dev backend (FastAPI gateway) runs on :8000. The Vite dev server proxies
// /api and /ws to it (see vite.config.ts), so same-origin paths work in dev.
// We still derive an absolute WS origin from window.location for robustness when
// the page is served from a non-proxied origin.
const HTTP_BASE = "/api/v1";

function wsOrigin(): string {
  // In dev, Vite proxies /ws → http://localhost:8000, so same-origin is fine.
  // If you serve this page from somewhere the proxy isn't wired, point at :8000.
  const loc = window.location;
  const proto = loc.protocol === "https:" ? "wss:" : "ws:";
  // Same-origin (proxied). To hardcode the dev backend instead, use:
  //   return "ws://localhost:8000";
  return `${proto}//${loc.host}`;
}

export async function createThread(displayName?: string): Promise<CreateThreadResponse> {
  const res = await fetch(`${HTTP_BASE}/inbound/threads`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(displayName ? { display_name: displayName } : {}),
  });
  if (!res.ok) throw new Error(`createThread failed: ${res.status}`);
  return (await res.json()) as CreateThreadResponse;
}

/** REST fallback send (used only when the socket is not open). */
export async function postMessage(threadId: string, text: string, displayName?: string): Promise<void> {
  const res = await fetch(`${HTTP_BASE}/inbound/threads/${encodeURIComponent(threadId)}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(displayName ? { text, display_name: displayName } : { text }),
  });
  if (!res.ok) throw new Error(`postMessage failed: ${res.status}`);
}

export interface WidgetSocketHandlers {
  onEvent: (ev: WsInboundEvent) => void;
  onOpen?: () => void;
  onClose?: () => void;
}

/**
 * Connect the public widget socket with light auto-reconnect.
 * Returns a controller: `send` (returns true if delivered over the socket) and
 * `close` to tear down (disables reconnect).
 */
export function connectWidgetSocket(wsUrl: string, handlers: WidgetSocketHandlers) {
  let ws: WebSocket | null = null;
  let closed = false;
  let retry = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  const url = `${wsOrigin()}${wsUrl}`;

  const open = () => {
    if (closed) return;
    ws = new WebSocket(url);

    ws.onopen = () => {
      retry = 0;
      handlers.onOpen?.();
    };
    ws.onmessage = (e) => {
      try {
        const parsed = JSON.parse(e.data) as WsInboundEvent;
        if (parsed && (parsed.type === "message" || parsed.type === "status")) {
          handlers.onEvent(parsed);
        }
      } catch {
        /* ignore non-JSON frames */
      }
    };
    ws.onclose = () => {
      handlers.onClose?.();
      if (closed) return;
      const delay = Math.min(1000 * 2 ** retry, 8000);
      retry += 1;
      reconnectTimer = setTimeout(open, delay);
    };
    ws.onerror = () => {
      // Let onclose drive the reconnect.
      ws?.close();
    };
  };

  open();

  return {
    /** Send over the socket; returns true if it went out, false if not open. */
    send(text: string): boolean {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "message", data: { text } }));
        return true;
      }
      return false;
    },
    isOpen(): boolean {
      return !!ws && ws.readyState === WebSocket.OPEN;
    },
    close() {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    },
  };
}

export type WidgetSocket = ReturnType<typeof connectWidgetSocket>;

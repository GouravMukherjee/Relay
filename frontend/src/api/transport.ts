// Transport abstraction over the per-session event stream — the WebSocket
// (/ws/sessions/{id}) carries transcript / card / status events to the UI.

import type { ClientEvent, ServerEvent } from "../types";

export interface RelayTransport {
  onEvent(cb: (e: ServerEvent) => void): void;
  send(e: ClientEvent): void;
  close(): void;
}

// ── Real WebSocket transport ───────────────────────────────────────────────────
// Connects to the backend WS, queues sends until open, surfaces connection loss as
// `error` / `session.status` events, and transparently reconnects with backoff.
export class WsTransport implements RelayTransport {
  private ws: WebSocket | null = null;
  private listeners = new Set<(e: ServerEvent) => void>();
  private queue: ClientEvent[] = [];
  private closedByUs = false;
  private retries = 0;

  constructor(private url: string) {
    this.connect();
  }

  private connect() {
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.url);
    } catch (e) {
      this.emit({
        type: "error",
        ts: new Date().toISOString(),
        data: { code: "ws_connect_failed", message: (e as Error)?.message ?? "bad WebSocket URL" },
      });
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      this.retries = 0;
      this.queue.forEach((e) => ws.send(JSON.stringify(e)));
      this.queue = [];
    };

    ws.onmessage = (msg) => {
      try {
        this.emit(JSON.parse(msg.data) as ServerEvent);
      } catch {
        /* ignore malformed frames */
      }
    };

    ws.onerror = () => {
      this.emit({
        type: "error",
        ts: new Date().toISOString(),
        data: { code: "ws_error", message: `WebSocket error (${this.url})` },
      });
    };

    ws.onclose = () => {
      if (this.closedByUs) return;
      this.emit({
        type: "session.status",
        ts: new Date().toISOString(),
        data: { status: "ended", retrieval_backend: "moss" },
      });
      // Reconnect with capped exponential backoff.
      if (this.retries < 5) {
        const delay = Math.min(1000 * 2 ** this.retries, 10000);
        this.retries++;
        setTimeout(() => !this.closedByUs && this.connect(), delay);
      } else {
        this.emit({
          type: "error",
          ts: new Date().toISOString(),
          data: { code: "ws_unreachable", message: `Lost connection to ${this.url}` },
        });
      }
    };
  }

  onEvent(cb: (e: ServerEvent) => void) {
    this.listeners.add(cb);
  }

  send(e: ClientEvent) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(e));
    else this.queue.push(e);
  }

  close() {
    this.closedByUs = true;
    this.listeners.clear();
    this.ws?.close();
  }

  private emit(e: ServerEvent) {
    this.listeners.forEach((cb) => cb(e));
  }
}

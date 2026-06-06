// Transport abstraction over the per-session event stream. Both the real
// WebSocket (/ws/sessions/{id}) and the in-browser demo engine implement this,
// so the rest of the app is agnostic to whether a backend is present.

import type { ClientEvent, ServerEvent } from "../types";

export interface RelayTransport {
  onEvent(cb: (e: ServerEvent) => void): void;
  send(e: ClientEvent): void;
  close(): void;
}

// ── Real WebSocket transport ───────────────────────────────────────────────────
export class WsTransport implements RelayTransport {
  private ws: WebSocket;
  private listeners = new Set<(e: ServerEvent) => void>();
  private queue: ClientEvent[] = [];

  constructor(url: string) {
    this.ws = new WebSocket(url);
    this.ws.onopen = () => {
      this.queue.forEach((e) => this.ws.send(JSON.stringify(e)));
      this.queue = [];
    };
    this.ws.onmessage = (msg) => {
      try {
        const evt = JSON.parse(msg.data) as ServerEvent;
        this.listeners.forEach((cb) => cb(evt));
      } catch {
        /* ignore malformed frames */
      }
    };
  }

  onEvent(cb: (e: ServerEvent) => void) {
    this.listeners.add(cb);
  }

  send(e: ClientEvent) {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(e));
    else this.queue.push(e);
  }

  close() {
    this.listeners.clear();
    this.ws.close();
  }
}

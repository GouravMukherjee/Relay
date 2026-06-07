// Minimal, self-contained types for the Northwind customer widget.
// Deliberately NOT importing the rep app's types — this is the customer side and
// only speaks the public inbound contract (docs/INBOUND_CONTRACT.md).

export type Role = "customer" | "agent";
export type Department = "desk" | "intake";

/** Server → widget WS events (frozen contract). */
export interface WsMessageEvent {
  type: "message";
  ts?: string;
  data: { role: Role; text: string };
}
export interface WsStatusEvent {
  type: "status";
  ts?: string;
  data: { routed_to: Department; agent_typing: boolean };
}
export type WsInboundEvent = WsMessageEvent | WsStatusEvent;

/** POST /inbound/threads response. */
export interface CreateThreadResponse {
  thread_id: string;
  ws_url: string;
}

/** A rendered chat message in the widget. */
export interface ChatMessage {
  id: string;
  role: Role;
  text: string;
  ts: number; // epoch ms (local clock for optimistic, server ts otherwise)
  optimistic?: boolean;
}

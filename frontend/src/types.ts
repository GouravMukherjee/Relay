// Shared object shapes — mirror API_SPEC.md exactly. This is the frozen contract
// between frontend, backend, and the agent worker. Do not drift from the spec.

export type Mode = "live" | "desk" | "intake";

export type RetrievalBackend = "moss" | "pgvector";

export interface Source {
  document_id: string; // doc_…
  title: string;
  snippet: string;
  score: number; // retrieval score, 0..1
}

export interface Card {
  card_id: string; // card_…
  session_id: string; // ses_…
  mode: Mode;
  title?: string; // short headline for the card (e.g. "99.9% uptime SLA")
  answer: string;
  sources: Source[];
  trigger_text: string;
  latency_ms: number;
  created_at: string; // ISO-8601 UTC
}

export interface Utterance {
  utterance_id: string; // utt_…
  session_id: string; // ses_…
  speaker: string; // e.g. rep | prospect | customer
  text: string;
  ts: string; // ISO-8601 UTC
}

export interface LeadQualifiers {
  budget?: string;
  timeline?: string;
  need?: string;
  [k: string]: string | undefined;
}

export type LeadStatus = "hot" | "warm" | "cold";

export interface Lead {
  lead_id: string; // lead_…
  session_id: string; // ses_…
  name: string;
  company: string;
  email: string;
  qualifiers: LeadQualifiers;
  score: number; // 0..100 (ICP fit)
  status: LeadStatus;
  routed_to: string | null;
  created_at: string;
}

export interface DocumentRecord {
  document_id: string; // doc_…
  title: string;
  status: "processing" | "ready" | "failed";
  chunk_count: number;
  created_at: string;
}

export interface SessionInfo {
  session_id: string;
  mode: Mode;
  status: "active" | "ended";
  started_at: string;
  ended_at?: string | null;
  card_count: number;
}

// ── WebSocket envelope (server↔client) ────────────────────────────────────────
// { "type": string, "ts": ISO-8601, "data": {} }

export type ServerEvent =
  | { type: "transcript.partial"; ts: string; data: { speaker: string; text: string } }
  | {
      type: "transcript.final";
      ts: string;
      data: { utterance_id: string; speaker: string; text: string };
    }
  | { type: "card.new"; ts: string; data: Card }
  | { type: "card.update"; ts: string; data: { card_id: string } & Partial<Card> }
  | {
      type: "session.status";
      ts: string;
      data: { status: "active" | "ended"; retrieval_backend: RetrievalBackend };
    }
  | { type: "lead.update"; ts: string; data: Lead } // Intake (not in base spec; additive)
  | { type: "error"; ts: string; data: { code: string; message: string } };

export type ClientEvent =
  | { type: "mode.set"; data: { mode: Mode } }
  | { type: "query.manual"; data: { text: string } }
  | { type: "card.pin"; data: { card_id: string } }
  | { type: "card.dismiss"; data: { card_id: string } };

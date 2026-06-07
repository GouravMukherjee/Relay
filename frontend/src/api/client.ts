// REST client for the Relay gateway. Endpoints mirror API_SPEC.md.
// All bodies are JSON unless noted (document upload is multipart).

import { API_BASE } from "../config";
import type { Card, DocumentRecord, Lead, Mode, SessionInfo, Utterance } from "../types";

// Token provider: set this to a function that returns the current JWT so that
// req() and uploadDocument can inject "Authorization: Bearer <token>".
// Wired up by AuthContext when VITE_USE_MOCK=false; no-op in mock mode.
let _getToken: (() => string | null) | null = null;

export function setTokenProvider(fn: () => string | null): void {
  _getToken = fn;
}

function authHeaders(): Record<string, string> {
  const token = _getToken?.();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export interface ApiError {
  code: string;
  message: string;
}

// Shapes for endpoints that extend the frozen API_SPEC (see "additive" section).
export interface User {
  id: string; // usr_…
  name: string;
  role: string;
  email?: string;
}

export interface Notification {
  id: string;
  text: string;
  read: boolean;
  created_at: string;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    let err: ApiError = { code: "internal_error", message: res.statusText };
    try {
      const body = await res.json();
      if (body?.error) err = body.error;
    } catch {
      /* non-JSON error body */
    }
    throw err;
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  // ── Documents ──────────────────────────────────────────────────────────────
  listDocuments: () => req<{ documents: DocumentRecord[] }>("/documents"),

  getDocument: (id: string) => req<DocumentRecord>(`/documents/${id}`),

  uploadDocument: async (file: File, title?: string, tags?: string[]) => {
    const form = new FormData();
    form.append("file", file);
    if (title) form.append("title", title);
    if (tags) tags.forEach((t) => form.append("tags", t));
    const res = await fetch(`${API_BASE}/documents`, {
      method: "POST",
      body: form,
      headers: authHeaders(),
    });
    if (!res.ok) throw await res.json().catch(() => ({ code: "internal_error", message: "" }));
    return (await res.json()) as { document_id: string; status: string };
  },

  deleteDocument: (id: string) => req<void>(`/documents/${id}`, { method: "DELETE" }),

  // ── Whisper-back TTS (MiniMax) ───────────────────────────────────────────────
  // Returns a playable object URL for the synthesized MP3, or throws ApiError.
  ttsUrl: async (text: string, voiceId?: string): Promise<string> => {
    const res = await fetch(`${API_BASE}/tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ text, voice_id: voiceId }),
    });
    if (!res.ok) throw await res.json().catch(() => ({ code: "internal_error", message: "TTS failed" }));
    const blob = await res.blob();
    return URL.createObjectURL(blob);
  },

  // ── Sessions ───────────────────────────────────────────────────────────────
  createSession: (mode: Mode, opts?: { livekit_room?: string; customer_id?: string }) =>
    req<{ session_id: string; ws_url: string; livekit_token?: string }>("/sessions", {
      method: "POST",
      body: JSON.stringify({ mode, ...opts }),
    }),

  getSession: (id: string) => req<SessionInfo>(`/sessions/${id}`),

  endSession: (id: string) =>
    req<{ status: "ended" }>(`/sessions/${id}/end`, { method: "POST" }),

  getCards: (id: string) => req<{ cards: Card[] }>(`/sessions/${id}/cards`),

  getTranscript: (id: string) =>
    req<{ utterances: Utterance[] }>(`/sessions/${id}/transcript`),

  // ── Query (manual fallback / Desk) ───────────────────────────────────────────
  query: (body: { session_id?: string; mode: Mode; text: string; customer_id?: string }) =>
    req<{ card: Card | null }>("/query", { method: "POST", body: JSON.stringify(body) }),

  // ── Leads (Intake) ───────────────────────────────────────────────────────────
  listLeads: () => req<{ leads: Lead[] }>("/leads"),
  getLead: (id: string) => req<Lead>(`/leads/${id}`),
  routeLead: (id: string) =>
    req<{ routed_to: string }>(`/leads/${id}/route`, { method: "POST" }),

  // ── Additive endpoints ───────────────────────────────────────────────────────
  // Beyond the frozen API_SPEC, but following the same conventions. These back
  // the dashboard chrome (nav tabs, account, notifications) and per-mode actions.
  // The backend (FastAPI gateway, see TECHNICAL_DESIGN §3.8) is expected to add
  // these; until then the UI degrades gracefully in demo mode.

  // Account / chrome
  getMe: () => req<User>("/me"),
  listUsers: () => req<{ users: User[] }>("/users"), // Team tab
  listSessions: () => req<{ sessions: SessionInfo[] }>("/sessions"), // Transcripts tab
  listNotifications: () => req<{ notifications: Notification[] }>("/notifications"),

  // Realtime audio: mint a LiveKit token to (re)join the room for a session.
  livekitToken: (sessionId: string) =>
    req<{ livekit_token: string; livekit_room: string }>(`/sessions/${sessionId}/livekit-token`, {
      method: "POST",
    }),

  // Desk: send the suggested resolution (optionally edited) back to the customer.
  sendReply: (sessionId: string, body: { card_id?: string; text: string }) =>
    req<{ status: "sent" }>(`/sessions/${sessionId}/reply`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // Intake: book a meeting for a qualified lead.
  bookMeeting: (leadId: string) =>
    req<{ status: "booked"; calendar_url?: string }>(`/leads/${leadId}/book`, { method: "POST" }),
};

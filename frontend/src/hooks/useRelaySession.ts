// Owns a Relay session: creates it, wires the event transport (real WS or the
// demo engine), and exposes reactive state + actions to the UI.

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { WsTransport, type RelayTransport } from "../api/transport";
import { MockEngine } from "../mock/engine";
import { USE_MOCK, wsUrl } from "../config";
import type {
  Card,
  Lead,
  Mode,
  RetrievalBackend,
  ServerEvent,
  Utterance,
} from "../types";

export interface PartialLine {
  speaker: string;
  text: string;
}

export interface RelaySessionState {
  sessionId: string | null;
  status: "connecting" | "active" | "ended";
  backend: RetrievalBackend;
  mode: Mode;
  utterances: Utterance[];
  partial: PartialLine | null;
  cards: Card[];
  pinned: Set<string>;
  dismissed: Set<string>;
  lead: Lead | null;
  lastError: string | null;
}

export function useRelaySession(initialMode: Mode) {
  const [state, setState] = useState<RelaySessionState>({
    sessionId: null,
    status: "connecting",
    backend: "moss",
    mode: initialMode,
    utterances: [],
    partial: null,
    cards: [],
    pinned: new Set(),
    dismissed: new Set(),
    lead: null,
    lastError: null,
  });

  const transportRef = useRef<RelayTransport | null>(null);
  const engineRef = useRef<MockEngine | null>(null);

  const handleEvent = useCallback((e: ServerEvent) => {
    setState((s) => {
      switch (e.type) {
        case "session.status":
          return { ...s, status: e.data.status === "ended" ? "ended" : "active", backend: e.data.retrieval_backend };
        case "transcript.partial":
          return { ...s, partial: { speaker: e.data.speaker, text: e.data.text } };
        case "transcript.final":
          return {
            ...s,
            partial: null,
            utterances: [
              ...s.utterances,
              {
                utterance_id: e.data.utterance_id,
                session_id: s.sessionId ?? "",
                speaker: e.data.speaker,
                text: e.data.text,
                ts: e.ts,
              },
            ],
          };
        case "card.new":
          if (s.cards.some((c) => c.card_id === e.data.card_id)) return s;
          return { ...s, cards: [e.data, ...s.cards] };
        case "card.update":
          return {
            ...s,
            cards: s.cards.map((c) => (c.card_id === e.data.card_id ? { ...c, ...e.data } : c)),
          };
        case "lead.update":
          return { ...s, lead: e.data };
        case "error":
          return { ...s, lastError: e.data.message };
        default:
          return s;
      }
    });
  }, []);

  // Establish session + transport once on mount.
  useEffect(() => {
    let disposed = false;

    async function start() {
      let sessionId: string;
      let transport: RelayTransport;

      if (USE_MOCK) {
        sessionId = `ses_demo${Math.random().toString(36).slice(2, 8)}`;
        const engine = new MockEngine(sessionId, initialMode);
        engineRef.current = engine;
        transport = engine;
      } else {
        // Functional mode: create a session on the backend, then open its WS.
        try {
          const res = await api.createSession(initialMode);
          sessionId = res.session_id;
          transport = new WsTransport(wsUrl(res.ws_url));
        } catch (e) {
          if (disposed) return;
          const msg = (e as { message?: string })?.message ?? "request failed";
          setState((s) => ({
            ...s,
            status: "ended",
            lastError: `Can't reach backend — ${msg}`,
          }));
          return;
        }
      }

      if (disposed) {
        transport.close();
        return;
      }
      transport.onEvent(handleEvent);
      transportRef.current = transport;
      setState((s) => ({ ...s, sessionId, lastError: null }));
    }

    void start();
    return () => {
      disposed = true;
      transportRef.current?.close();
      transportRef.current = null;
      engineRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Actions ──────────────────────────────────────────────────────────────────

  const setMode = useCallback((mode: Mode) => {
    setState((s) => ({ ...s, mode, utterances: [], cards: [], partial: null, lead: null }));
    transportRef.current?.send({ type: "mode.set", data: { mode } });
  }, []);

  const sendQuery = useCallback((text: string) => {
    transportRef.current?.send({ type: "query.manual", data: { text } });
  }, []);

  const pinCard = useCallback((card_id: string) => {
    setState((s) => {
      const pinned = new Set(s.pinned);
      pinned.has(card_id) ? pinned.delete(card_id) : pinned.add(card_id);
      return { ...s, pinned };
    });
    transportRef.current?.send({ type: "card.pin", data: { card_id } });
  }, []);

  const dismissCard = useCallback((card_id: string) => {
    setState((s) => {
      const dismissed = new Set(s.dismissed);
      dismissed.add(card_id);
      return { ...s, dismissed };
    });
    transportRef.current?.send({ type: "card.dismiss", data: { card_id } });
  }, []);

  const routeLead = useCallback(() => {
    setState((s) => {
      if (!s.lead) return s;
      const routed = { ...s.lead, routed_to: "#sales" };
      if (!USE_MOCK && s.lead.lead_id !== "lead_draft") void api.routeLead(s.lead.lead_id);
      return { ...s, lead: routed };
    });
  }, []);

  // Demo-only: drive the rehearsed script. No-op against a real backend.
  const playNextBeat = useCallback(async () => {
    if (engineRef.current) return engineRef.current.playNextBeat();
    return false;
  }, []);

  const canPlayBeat = !!engineRef.current;

  return { state, setMode, sendQuery, pinCard, dismissCard, routeLead, playNextBeat, canPlayBeat };
}

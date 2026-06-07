// Owns a Relay session: creates it on the backend, wires the WebSocket transport
// (and LiveKit audio), and exposes reactive state + actions to the UI.

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { WsTransport, type RelayTransport } from "../api/transport";
import { LIVEKIT_URL, wsUrl } from "../config";

// Token injector for WS URL. Set externally by AuthContext.
let _wsGetToken: (() => string | null) | null = null;
export function setWsTokenProvider(fn: () => string | null): void {
  _wsGetToken = fn;
}
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
  // Live audio: whether the mic track is currently un-muted, and whether a
  // LiveKit room is connected at all (false when audio failed to join).
  micEnabled: boolean;
  micAvailable: boolean;
  // Epoch ms when the current session's call started — drives the call timer.
  startedAt: number | null;
  // Live source: "phone" = watch the inbound-phone demo room (default); "mic" =
  // publish the browser mic (fallback). Only meaningful in Live mode.
  liveSource: LiveSource;
  // Inbound-call indicator: true while a SIP caller is on the line.
  callActive: boolean;
  callKind: string | null; // "sip" | "browser" | null
}

export type LiveSource = "phone" | "mic";

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
    micEnabled: false,
    micAvailable: false,
    startedAt: null,
    liveSource: "phone",
    callActive: false,
    callKind: null,
  });

  const transportRef = useRef<RelayTransport | null>(null);
  // LiveKit room: publishes the mic so the agent worker transcribes live audio.
  const roomRef = useRef<import("livekit-client").Room | null>(null);
  // Bumped to force the session effect to re-run (New Session button → fresh room).
  const [restartKey, setRestartKey] = useState(0);
  // Selected live source, mirrored into a ref so the session effect closure sees it.
  const [liveSource, setLiveSourceState] = useState<LiveSource>("phone");
  const liveSourceRef = useRef<LiveSource>("phone");
  useEffect(() => {
    liveSourceRef.current = liveSource;
  }, [liveSource]);

  const handleEvent = useCallback((e: ServerEvent) => {
    setState((s) => {
      switch (e.type) {
        case "session.status": {
          const raw = e.data.status as string;
          const status = raw === "ended" ? "ended" : raw === "reconnecting" ? "connecting" : "active";
          // Inbound-call indicator. When a SIP caller joins (call_active flips true),
          // (re)anchor the timer to the call start so it reads call duration.
          const callActive = e.data.call_active ?? s.callActive;
          const callKind = e.data.call_kind ?? s.callKind;
          const justConnected = e.data.call_active === true && !s.callActive;
          return {
            ...s,
            status,
            backend: e.data.retrieval_backend ?? s.backend,
            callActive,
            callKind,
            startedAt: justConnected ? Date.now() : s.startedAt,
          };
        }
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

  // Current mode, mirrored into a ref so a restart re-creates the session in the
  // SAME mode (the effect closure can't see later state updates).
  const modeRef = useRef<Mode>(initialMode);
  useEffect(() => {
    modeRef.current = state.mode;
  }, [state.mode]);

  // Establish session + transport on mount, and again whenever restartKey bumps
  // (the "New Session" button). A restart clears transcript/cards/timer in place —
  // single-page, no reload, no new tab.
  useEffect(() => {
    let disposed = false;

    // Reset per-session state for a fresh start (keep mode, pins are session-scoped).
    setState((s) => ({
      ...s,
      sessionId: null,
      status: "connecting",
      utterances: [],
      partial: null,
      cards: [],
      pinned: new Set(),
      dismissed: new Set(),
      lead: null,
      micEnabled: false,
      micAvailable: false,
      startedAt: null,
      callActive: false,
      callKind: null,
    }));

    async function start() {
      let sessionId: string;
      let transport: RelayTransport;
      let micEnabled = false;
      let micAvailable = false;

      // In Live mode the default source is the inbound-phone demo room: WATCH it (the
      // agent streams cards for the SIP caller) without publishing the browser mic.
      // Any other case (Desk/Intake, or Live with the "mic" fallback) creates a normal
      // session and — for mic — joins the room and publishes audio.
      const watchPhone = modeRef.current === "live" && liveSourceRef.current === "phone";

      try {
        let wsPath: string;
        let livekitToken: string | undefined;

        if (watchPhone) {
          const demo = await api.getDemoSession();
          sessionId = demo.session_id;
          wsPath = demo.ws_url;
          livekitToken = undefined; // watch-only: do not publish mic on the phone path
        } else {
          const res = await api.createSession(modeRef.current);
          sessionId = res.session_id;
          wsPath = res.ws_url;
          livekitToken = res.livekit_token;
        }

        const rawWsUrl = wsUrl(wsPath);
        const token = _wsGetToken?.();
        const fullWsUrl = token
          ? `${rawWsUrl}${rawWsUrl.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`
          : rawWsUrl;
        transport = new WsTransport(fullWsUrl);

        // Mic fallback path: join the LiveKit room and publish the mic so the agent
        // worker transcribes live audio. Audio failure is non-fatal — the session
        // still works with manual queries + the card/transcript WS.
        if (livekitToken && LIVEKIT_URL) {
          try {
            const { Room } = await import("livekit-client");
            const room = new Room();
            await room.connect(LIVEKIT_URL, livekitToken);
            if (disposed) {
              await room.disconnect();
              return;
            }
            await room.localParticipant.setMicrophoneEnabled(true);
            roomRef.current = room;
            micAvailable = true;
            micEnabled = true;
          } catch (audioErr) {
            console.warn("Relay: LiveKit audio unavailable —", audioErr);
          }
        }
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

      if (disposed) {
        transport.close();
        return;
      }
      transport.onEvent(handleEvent);
      transportRef.current = transport;
      // startedAt anchors the call timer to this session's start (00:00 on a fresh session).
      setState((s) => ({
        ...s,
        sessionId,
        lastError: null,
        micEnabled,
        micAvailable,
        startedAt: Date.now(),
      }));
    }

    void start();
    return () => {
      disposed = true;
      transportRef.current?.close();
      transportRef.current = null;
      void roomRef.current?.disconnect();
      roomRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restartKey, liveSource]);

  // ── Actions ──────────────────────────────────────────────────────────────────

  const setMode = useCallback((mode: Mode) => {
    setState((s) => ({ ...s, mode, utterances: [], cards: [], partial: null, lead: null }));
    transportRef.current?.send({ type: "mode.set", data: { mode } });
  }, []);

  const sendQuery = useCallback((text: string, opts?: { customer_id?: string }) => {
    transportRef.current?.send({
      type: "query.manual",
      data: { text, ...(opts?.customer_id ? { customer_id: opts.customer_id } : {}) },
    });
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
      void api.routeLead(s.lead.lead_id);
      return { ...s, lead: { ...s.lead, routed_to: "#sales" } };
    });
  }, []);

  // Mute / un-mute the EXISTING mic track. setMicrophoneEnabled() toggles the
  // published track's mute state — it does NOT unpublish or stop it — so the
  // LiveKit room + agent session stay alive and un-muting resumes instantly with
  // no reconnect or page refresh.
  const toggleMic = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    const next = !room.localParticipant.isMicrophoneEnabled;
    try {
      await room.localParticipant.setMicrophoneEnabled(next);
      setState((s) => ({ ...s, micEnabled: next }));
    } catch (e) {
      console.warn("Relay: mic toggle failed —", e);
    }
  }, []);

  // Start a FRESH session in place (new room, cleared transcript/cards/timer).
  // Single-page: bumps restartKey so the session effect tears down and re-runs.
  const restart = useCallback(() => {
    setRestartKey((k) => k + 1);
  }, []);

  // Switch the Live source between watching the inbound-phone demo room ("phone")
  // and publishing the browser mic ("mic"). Changing it re-runs the session effect,
  // which tears down the old connection and establishes the new source.
  const setLiveSource = useCallback((source: LiveSource) => {
    setState((s) => ({ ...s, liveSource: source }));
    setLiveSourceState(source);
  }, []);

  return {
    state,
    setMode,
    sendQuery,
    pinCard,
    dismissCard,
    routeLead,
    toggleMic,
    restart,
    setLiveSource,
  };
}

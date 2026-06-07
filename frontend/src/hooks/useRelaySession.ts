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
  /** True while the local microphone is publishing to the LiveKit room. */
  micEnabled: boolean;
  /** True while connecting/disconnecting the LiveKit room (debounces the toggle). */
  micBusy: boolean;
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
    micEnabled: false,
    micBusy: false,
  });

  const transportRef = useRef<RelayTransport | null>(null);
  // LiveKit room: publishes the mic so the agent worker transcribes live audio.
  // Created lazily on the first toggleMic() — NEVER on mount, so simply opening
  // the app does not publish audio (which would run STT and burn credits).
  const roomRef = useRef<import("livekit-client").Room | null>(null);
  // LiveKit access token from createSession, stashed for the lazy room connect.
  const livekitTokenRef = useRef<string | null>(null);
  // <audio> elements created for subscribed remote tracks, so we can detach them.
  const audioElsRef = useRef<HTMLMediaElement[]>([]);
  // Guards against overlapping connect/disconnect from rapid button clicks.
  const micBusyRef = useRef(false);

  const handleEvent = useCallback((e: ServerEvent) => {
    setState((s) => {
      switch (e.type) {
        case "session.status": {
          const raw = e.data.status as string;
          const status = raw === "ended" ? "ended" : raw === "reconnecting" ? "connecting" : "active";
          return { ...s, status, backend: e.data.retrieval_backend };
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

  // Establish session + transport once on mount.
  useEffect(() => {
    let disposed = false;

    async function start() {
      let sessionId: string;
      let transport: RelayTransport;

      // Create a session on the backend, then open its WS (passing the auth token
      // as a connect param when present).
      try {
        const res = await api.createSession(initialMode);
        sessionId = res.session_id;
        const rawWsUrl = wsUrl(res.ws_url);
        const token = _wsGetToken?.();
        const fullWsUrl = token
          ? `${rawWsUrl}${rawWsUrl.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`
          : rawWsUrl;
        transport = new WsTransport(fullWsUrl);

        // Stash the LiveKit token for an explicit, user-initiated mic start.
        // We DELIBERATELY do not connect the room or enable the mic here:
        // publishing audio on mount runs STT continuously and burns credits,
        // and the user has no way to consent. Live audio starts only when the
        // user clicks the mic button (see toggleMic).
        livekitTokenRef.current = res.livekit_token ?? null;
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
      setState((s) => ({ ...s, sessionId, lastError: null }));
    }

    void start();
    return () => {
      disposed = true;
      transportRef.current?.close();
      transportRef.current = null;
      // Tear down LiveKit so the mic stops publishing and remote audio stops
      // playing when the session unmounts (mode switch, sign-out, navigation).
      audioElsRef.current.forEach((el) => el.remove());
      audioElsRef.current = [];
      void roomRef.current?.disconnect();
      roomRef.current = null;
      micBusyRef.current = false;
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

  // Start/stop live audio. This is the ONLY place the LiveKit room is created or
  // the mic is enabled — turning it off fully disconnects the room so no audio is
  // published (STT stops) and remote playback ends. Idempotent under rapid clicks.
  const toggleMic = useCallback(async () => {
    if (micBusyRef.current) return; // ignore clicks while a transition is in flight
    const room = roomRef.current;

    // ── Stop: disconnect the room, detach audio, drop the mic. ──
    if (room) {
      micBusyRef.current = true;
      setState((s) => ({ ...s, micBusy: true }));
      try {
        await room.disconnect();
      } catch {
        /* best-effort */
      }
      audioElsRef.current.forEach((el) => el.remove());
      audioElsRef.current = [];
      roomRef.current = null;
      micBusyRef.current = false;
      setState((s) => ({ ...s, micEnabled: false, micBusy: false }));
      return;
    }

    // ── Start: connect, subscribe to remote audio, publish the mic. ──
    const token = livekitTokenRef.current;
    if (!token || !LIVEKIT_URL) {
      setState((s) => ({
        ...s,
        lastError: "Live audio isn't configured for this session.",
      }));
      return;
    }

    micBusyRef.current = true;
    setState((s) => ({ ...s, micBusy: true }));
    try {
      const { Room, RoomEvent, Track } = await import("livekit-client");
      const r = new Room();

      // Play subscribed remote audio (the live conversation / whisper-back) by
      // attaching each audio track to a hidden <audio> element. Without this the
      // tracks are received but never heard.
      r.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === Track.Kind.Audio) {
          const el = track.attach();
          el.autoplay = true;
          el.style.display = "none";
          document.body.appendChild(el);
          audioElsRef.current.push(el);
        }
      });
      r.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach().forEach((el) => {
          el.remove();
          audioElsRef.current = audioElsRef.current.filter((e) => e !== el);
        });
      });
      // If the room drops server-side, reflect that the mic is no longer live.
      r.on(RoomEvent.Disconnected, () => {
        audioElsRef.current.forEach((el) => el.remove());
        audioElsRef.current = [];
        roomRef.current = null;
        setState((s) => ({ ...s, micEnabled: false }));
      });

      await r.connect(LIVEKIT_URL, token);
      // Browsers gate autoplay until a user gesture — this toggle is one, so
      // unblock playback of the subscribed remote audio.
      try {
        await r.startAudio();
      } catch {
        /* will play once the browser allows it */
      }
      await r.localParticipant.setMicrophoneEnabled(true);
      roomRef.current = r;
      micBusyRef.current = false;
      setState((s) => ({ ...s, micEnabled: true, micBusy: false, lastError: null }));
    } catch (e) {
      const msg = (e as { message?: string })?.message ?? "audio failed";
      void roomRef.current?.disconnect();
      roomRef.current = null;
      micBusyRef.current = false;
      setState((s) => ({
        ...s,
        micEnabled: false,
        micBusy: false,
        lastError: `Couldn't start live audio — ${msg}`,
      }));
    }
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

  return { state, setMode, sendQuery, pinCard, dismissCard, routeLead, toggleMic };
}

// In-browser demo engine implementing RelayTransport. It replays the
// DEMO_SCRIPT.md beats with realistic timing so Relay is fully demoable with no
// backend: streaming transcripts, sub-500ms grounded cards, mode switching,
// manual queries, and a scripted Intake lead. Swap to WsTransport when the
// gateway is live (see config.ts / VITE_USE_MOCK).

import type { Beat } from "./dataset";
import {
  DESK_BEATS,
  INTAKE_BEATS,
  INTAKE_LEAD,
  LIVE_BEATS,
  retrieve,
} from "./dataset";
import type { RelayTransport } from "../api/transport";
import type { Card, ClientEvent, Lead, Mode, ServerEvent } from "../types";

let seq = 0;
const id = (p: string) => `${p}_${Date.now().toString(36)}${(seq++).toString(36)}`;
const now = () => new Date().toISOString();
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

export class MockEngine implements RelayTransport {
  private listeners = new Set<(e: ServerEvent) => void>();
  private mode: Mode;
  private sessionId: string;
  private closed = false;
  private beatIndex = 0;
  private intakeQ: LeadDraft = { qualifiers: {} };

  constructor(sessionId: string, mode: Mode) {
    this.sessionId = sessionId;
    this.mode = mode;
    // Announce session up on the next tick so listeners attach first.
    queueMicrotask(() =>
      this.emit({
        type: "session.status",
        ts: now(),
        data: { status: "active", retrieval_backend: "moss" },
      }),
    );
  }

  onEvent(cb: (e: ServerEvent) => void) {
    this.listeners.add(cb);
  }

  send(e: ClientEvent) {
    switch (e.type) {
      case "mode.set":
        this.mode = e.data.mode;
        this.beatIndex = 0;
        this.intakeQ = { qualifiers: {} };
        this.emit({
          type: "session.status",
          ts: now(),
          data: { status: "active", retrieval_backend: "moss" },
        });
        break;
      case "query.manual":
        // Typed fallback — treated as a rep utterance, then retrieved.
        void this.speakAndRetrieve({ speaker: "rep", text: e.data.text });
        break;
      case "card.pin":
      case "card.dismiss":
        // Card actions are local UI state in the demo; nothing to ack.
        break;
    }
  }

  close() {
    this.closed = true;
    this.listeners.clear();
  }

  // ── Demo driving API (exposed to the UI's "play beat" control) ───────────────

  /** Advance the rehearsed script by one beat for the current mode. */
  async playNextBeat(): Promise<boolean> {
    const beats = this.beatsForMode();
    if (this.beatIndex >= beats.length) return false;
    const beat = beats[this.beatIndex++];
    await this.runBeat(beat);
    return this.beatIndex < beats.length;
  }

  hasMoreBeats(): boolean {
    return this.beatIndex < this.beatsForMode().length;
  }

  resetBeats() {
    this.beatIndex = 0;
    this.intakeQ = { qualifiers: {} };
  }

  // ── Internals ────────────────────────────────────────────────────────────────

  private beatsForMode(): Beat[] {
    return this.mode === "desk" ? DESK_BEATS : this.mode === "intake" ? INTAKE_BEATS : LIVE_BEATS;
  }

  private async runBeat(beat: Beat) {
    if (this.mode === "intake") {
      await this.streamTranscript(beat);
      if (beat.speaker === "caller") this.advanceIntake(beat.text);
    } else {
      await this.speakAndRetrieve(beat);
    }
  }

  private async speakAndRetrieve(beat: Beat) {
    await this.streamTranscript(beat);
    const t0 = performance.now();
    const entry = retrieve(beat.text, this.mode);
    // Simulate the retrieval+synthesis budget (Moss <10ms + Claude synth).
    const synth = 280 + Math.floor(Math.random() * 170); // ~280–450ms
    await wait(synth);
    if (this.closed) return;
    if (!entry) return; // grounded or silent — no hallucinated card

    const card: Card = {
      card_id: id("card"),
      session_id: this.sessionId,
      mode: this.mode,
      title: entry.title,
      answer: entry.answer,
      sources: entry.sources,
      trigger_text: beat.text,
      latency_ms: Math.round(performance.now() - t0),
      created_at: now(),
    };
    this.emit({ type: "card.new", ts: now(), data: card });
  }

  private async streamTranscript(beat: Beat) {
    const words = beat.text.split(" ");
    let acc = "";
    for (let i = 0; i < words.length; i++) {
      if (this.closed) return;
      acc += (i ? " " : "") + words[i];
      this.emit({
        type: "transcript.partial",
        ts: now(),
        data: { speaker: beat.speaker, text: acc },
      });
      await wait(45 + Math.random() * 40);
    }
    this.emit({
      type: "transcript.final",
      ts: now(),
      data: { utterance_id: id("utt"), speaker: beat.speaker, text: beat.text },
    });
  }

  private advanceIntake(text: string) {
    const q = text.toLowerCase();
    const m = this.intakeQ.qualifiers;
    if (/need|onboard|latency|looking|specs|replace|manual/.test(q))
      m.need = "Replacing manual onboarding, reducing latency";
    if (/vp|head|director|founder|ceo|cto|my call|decision/.test(q)) m.authority = "VP Eng (decision maker)";
    if (/\$|\bk\b|budget|year|annum/.test(q)) m.budget = "$40–60k / yr";
    if (/quarter|month|asap|q[1-4]|this year|soon|evaluat/.test(q)) m.timeline = "Evaluating this quarter";

    // Once we have enough signal, score and route the lead.
    const filled = Object.keys(m).length;
    const complete = filled >= 4;
    const lead: Lead = {
      lead_id: complete ? id("lead") : "lead_draft",
      session_id: this.sessionId,
      name: INTAKE_LEAD.name,
      company: INTAKE_LEAD.company,
      email: INTAKE_LEAD.email,
      qualifiers: { ...m },
      score: complete ? 82 : Math.min(78, 30 + filled * 14),
      status: complete ? "hot" : "warm",
      routed_to: null, // presenter clicks "Route to #sales" live
      created_at: now(),
    };
    this.emit({ type: "lead.update", ts: now(), data: lead });
  }

  private emit(e: ServerEvent) {
    if (this.closed) return;
    this.listeners.forEach((cb) => cb(e));
  }
}

interface LeadDraft {
  qualifiers: Record<string, string>;
}

import { useEffect, useRef } from "react";
import { motion } from "framer-motion";
import type { RelaySessionState } from "../hooks/useRelaySession";
import { api } from "../api/client";
import { useBackend } from "../backend";
import { Waveform } from "../components/Waveform";
import { Icon } from "../components/Icon";
import { clock, initials } from "../util";

interface Props {
  state: RelaySessionState;
  onRoute: () => void;
}

const QUALS: { key: "budget" | "authority" | "need" | "timeline"; label: string }[] = [
  { key: "budget", label: "Budget" },
  { key: "authority", label: "Authority" },
  { key: "need", label: "Need" },
  { key: "timeline", label: "Timeline" },
];

export function IntakeView({ state, onRoute }: Props) {
  const { call } = useBackend();
  const scrollRef = useRef<HTMLDivElement>(null);
  const last = state.utterances.length - 1;
  const lead = state.lead;
  const routed = !!lead?.routed_to;

  const onBook = () =>
    lead &&
    call("Book meeting", () => api.bookMeeting(lead.lead_id), {
      endpoint: `POST /leads/${lead.lead_id}/book`,
      success: "Meeting booked",
    });

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [state.utterances, state.partial]);

  return (
    <div className="split">
      {/* Left: inbound call */}
      <section className="col-left card-surface callpanel">
        <div className="callpanel-head">
          <div className="callpanel-head-row">
            <h2 className="label-caps call-status">
              <span className="live-dot" />
              Inbound Call
            </h2>
            <span className="mono call-timer">{new Date().toTimeString().slice(0, 5)}</span>
          </div>
          <Waveform active={!!state.partial} />
        </div>

        <div className="transcript scroll" ref={scrollRef}>
          {state.utterances.length === 0 && !state.partial && (
            <div className="empty-state" style={{ padding: 24 }}>
              <Icon name="support_agent" size={32} />
              <div className="small">The agent qualifies the caller, scores ICP fit, and routes hot leads.</div>
            </div>
          )}
          {state.utterances.map((u, i) => (
            <div className={`utt${i === last && u.speaker === "relay" ? " active" : ""}`} key={u.utterance_id}>
              <div className="utt-meta">
                <span className={`spk-tag ${u.speaker}`}>{cap(u.speaker)}</span>
                <span className="utt-time">{clock(u.ts)}</span>
              </div>
              <p className="utt-text">{u.text}</p>
            </div>
          ))}
          {state.partial && (
            <div className="utt">
              <div className="utt-meta">
                <span className={`spk-tag ${state.partial.speaker}`}>{cap(state.partial.speaker)}</span>
              </div>
              <p className="utt-text">{state.partial.text}</p>
            </div>
          )}
        </div>
      </section>

      {/* Right: lead + qualification */}
      <section className="col-right">
        <div className="stack scroll">
          {!lead ? (
            <div className="card-surface lead-card">
              <div className="empty-state" style={{ padding: 16 }}>
                <Icon name="person_search" size={32} />
                <div className="big">No lead yet</div>
                <div className="small">
                  As the caller answers, Relay extracts budget, authority, need &amp; timeline, scores ICP fit,
                  and routes hot leads.
                </div>
              </div>
            </div>
          ) : (
            <>
              <motion.div className="card-surface lead-card" layout>
                <div className="section-label label-caps" style={{ paddingBottom: 16 }}>
                  Lead
                </div>
                <div className="lead-head">
                  <div className="lead-avatar">{initials(lead.name)}</div>
                  <div className="lead-id">
                    <h3>{lead.name}</h3>
                    <div className="sub">
                      {lead.company} · {lead.email}
                    </div>
                  </div>
                  <div className="score-wrap">
                    <div className="score-ring" style={{ ["--p" as string]: lead.score }}>
                      <b>{lead.score}</b>
                    </div>
                    <span className={`temp-badge ${lead.status}`}>{lead.status}</span>
                  </div>
                </div>
              </motion.div>

              <div className="card-surface qual-card">
                <div className="label-caps">Qualification</div>
                {QUALS.map((q) => {
                  const v = lead.qualifiers[q.key];
                  return (
                    <div className="qual-row" key={q.key}>
                      <span className={`qual-check ${v ? "filled" : "empty"}`}>
                        <Icon name={v ? "check_circle" : "radio_button_unchecked"} size={20} fill={!!v} />
                      </span>
                      <div className="qual-body">
                        <div className="k">{q.label}</div>
                        <div className={`v${v ? "" : " pending"}`}>{v ?? "listening…"}</div>
                      </div>
                    </div>
                  );
                })}
              </div>

              <div className="intake-actions">
                {routed && (
                  <span className="routed-note">
                    <Icon name="check_circle" size={16} fill />
                    Routed to {lead.routed_to}
                  </span>
                )}
                <button className="btn-secondary" disabled={routed} onClick={onBook}>
                  <Icon name="calendar_month" size={16} />
                  Book meeting
                </button>
                <button className="btn-primary" onClick={onRoute} disabled={routed || lead.status !== "hot"}>
                  Route to #sales
                  <Icon name="send" size={16} />
                </button>
              </div>
            </>
          )}
        </div>
      </section>
    </div>
  );
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

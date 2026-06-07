import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { RelaySessionState } from "../hooks/useRelaySession";
import { api } from "../api/client";
import { useBackend } from "../backend";
import { Waveform } from "../components/Waveform";
import { Icon } from "../components/Icon";
import { CallTimer } from "../components/CallTimer";
import { clock, initials } from "../util";
import { fadeUp, inView, item, pressable, staggerParent } from "../motion";

interface Props {
  state: RelaySessionState;
  onRoute: () => void;
  onQuery: (text: string) => void;
}

const QUALS: { key: "budget" | "authority" | "need" | "timeline"; label: string }[] = [
  { key: "budget", label: "Budget" },
  { key: "authority", label: "Authority" },
  { key: "need", label: "Need" },
  { key: "timeline", label: "Timeline" },
];

export function IntakeView({ state, onRoute, onQuery }: Props) {
  const { call } = useBackend();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [text, setText] = useState("");
  const last = state.utterances.length - 1;
  const lead = state.lead;
  const routed = !!lead?.routed_to;

  const submit = () => {
    const t = text.trim();
    if (!t) return;
    onQuery(t);
    setText("");
  };

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
            <span className="mono call-timer">
              <CallTimer startedAt={state.startedAt} />
            </span>
          </div>
          <Waveform active={!!state.partial} />
        </div>

        <div className="transcript scroll" ref={scrollRef}>
          {state.utterances.length === 0 && !state.partial && (
            <motion.div className="empty-state" style={{ padding: 24 }} variants={fadeUp} initial="hidden" animate="show">
              <Icon name="support_agent" size={32} />
              <div className="small">The agent qualifies the caller, scores ICP fit, and routes hot leads.</div>
            </motion.div>
          )}
          <AnimatePresence initial={false}>
            {state.utterances.map((u, i) => (
              <motion.div
                className={`utt${i === last && u.speaker === "relay" ? " active" : ""}`}
                key={u.utterance_id}
                layout
                variants={item}
                initial="hidden"
                animate="show"
              >
                <div className="utt-meta">
                  <span className={`spk-tag ${u.speaker}`}>{cap(u.speaker)}</span>
                  <span className="utt-time">{clock(u.ts)}</span>
                </div>
                <p className="utt-text">{u.text}</p>
              </motion.div>
            ))}
          </AnimatePresence>
          {state.partial && (
            <motion.div className="utt" variants={item} initial="hidden" animate="show">
              <div className="utt-meta">
                <span className={`spk-tag ${state.partial.speaker}`}>{cap(state.partial.speaker)}</span>
              </div>
              <p className="utt-text">{state.partial.text}</p>
            </motion.div>
          )}
        </div>

        {/* Typed caller input — same extraction→scoring path as voice, no mic needed.
            The reliable way to drive Intake (and the demo safety net). */}
        <div className="ask-bar">
          <Icon name="record_voice_over" size={16} />
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            placeholder="Type what the caller said — e.g. “I'm the VP of Eng, budget ~$50k/yr, need this by Q3”"
          />
          <motion.button className="ask-send" onClick={submit} title="Capture" {...pressable}>
            <Icon name="send" size={16} />
          </motion.button>
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
              <motion.div className="card-surface lead-card" layout variants={fadeUp} initial="hidden" animate="show">
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
                    <motion.div
                      className="score-ring"
                      style={{ ["--p" as string]: lead.score }}
                      initial={{ scale: 0.7, opacity: 0 }}
                      animate={{ scale: 1, opacity: 1 }}
                      transition={{ type: "spring", stiffness: 300, damping: 18 }}
                    >
                      <b>{lead.score}</b>
                    </motion.div>
                    <span className={`temp-badge ${lead.status}`}>{lead.status}</span>
                  </div>
                </div>
              </motion.div>

              <motion.div
                className="card-surface qual-card"
                variants={fadeUp}
                initial="hidden"
                whileInView="show"
                viewport={inView}
              >
                <div className="label-caps">Qualification</div>
                <motion.div variants={staggerParent(0.08)} initial="hidden" animate="show">
                  {QUALS.map((q) => {
                    const v = lead.qualifiers[q.key];
                    return (
                      <motion.div className="qual-row" key={q.key} variants={item}>
                        <span className={`qual-check ${v ? "filled" : "empty"}`}>
                          <Icon name={v ? "check_circle" : "radio_button_unchecked"} size={20} fill={!!v} />
                        </span>
                        <div className="qual-body">
                          <div className="k">{q.label}</div>
                          <div className={`v${v ? "" : " pending"}`}>{v ?? "listening…"}</div>
                        </div>
                      </motion.div>
                    );
                  })}
                </motion.div>
              </motion.div>

              <div className="intake-actions">
                {routed && (
                  <motion.span className="routed-note" initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}>
                    <Icon name="check_circle" size={16} fill />
                    Routed to {lead.routed_to}
                  </motion.span>
                )}
                <motion.button className="btn-secondary" disabled={routed} onClick={onBook} {...pressable}>
                  <Icon name="calendar_month" size={16} />
                  Book meeting
                </motion.button>
                <motion.button
                  className="btn-primary"
                  onClick={onRoute}
                  disabled={routed || lead.status !== "hot"}
                  {...pressable}
                >
                  Route to #sales
                  <Icon name="send" size={16} />
                </motion.button>
              </div>
            </>
          )}
        </div>
      </section>
    </div>
  );
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

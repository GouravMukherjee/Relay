import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { RelaySessionState } from "../hooks/useRelaySession";
import { api } from "../api/client";
import { useBackend } from "../backend";
import { Waveform } from "../components/Waveform";
import { RelayCard } from "../components/RelayCard";
import { Icon } from "../components/Icon";
import { clock } from "../util";
import { easeOut, fadeUp, iconHover, item, pressable } from "../motion";

interface Props {
  state: RelaySessionState;
  onQuery: (text: string) => void;
}

export function LiveView({ state, onQuery }: Props) {
  const { call } = useBackend();
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [ask, setAsk] = useState("");
  const last = state.utterances.length - 1;

  const submitAsk = () => {
    const t = ask.trim();
    if (!t) return;
    onQuery(t);
    setAsk("");
  };

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [state.utterances, state.partial]);

  const onMic = () =>
    state.sessionId &&
    call("Join audio room", () => api.livekitToken(state.sessionId!), {
      endpoint: `POST /sessions/${state.sessionId}/livekit-token`,
      success: "Joined LiveKit room",
    });

  const onUpload = (file: File) =>
    call("Upload document", () => api.uploadDocument(file, file.name), {
      endpoint: "POST /documents",
      success: `Ingesting ${file.name}…`,
    });

  return (
    <div className="split">
      {/* Left: live call */}
      <motion.section
        className="col-left card-surface callpanel"
        initial={{ opacity: 0, x: -22 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.5, ease: easeOut }}
      >
        <div className="callpanel-head">
          <div className="callpanel-head-row">
            <h2 className="label-caps">Live Call</h2>
            <div className="call-status">
              <span className="mono call-timer">{new Date().toTimeString().slice(0, 5)}</span>
              <motion.button className="mic-btn" title="Join / toggle microphone" onClick={onMic} {...iconHover}>
                <Icon name="mic" size={18} />
                <span className="mic-dot" />
              </motion.button>
            </div>
          </div>
          <Waveform active={!!state.partial} />
          {/* Typed question — same retrieval→card path as voice, no mic needed.
              The reliable way to drive Live (and the demo safety net). */}
          <div className="ask-bar">
            <Icon name="search" size={16} />
            <input
              value={ask}
              onChange={(e) => setAsk(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submitAsk()}
              placeholder="Ask a question — e.g. “What's your uptime SLA?”"
            />
            <motion.button className="ask-send" onClick={submitAsk} title="Ask" {...pressable}>
              <Icon name="send" size={16} />
            </motion.button>
          </div>
        </div>

        <div className="transcript scroll" ref={scrollRef}>
          {state.utterances.length === 0 && !state.partial && (
            <motion.div className="empty-state" style={{ padding: 24 }} variants={fadeUp} initial="hidden" animate="show">
              <Icon name="hearing" size={32} />
              <div className="small">Listening for the conversation. Ask a question, or type one below.</div>
            </motion.div>
          )}
          <AnimatePresence initial={false}>
            {state.utterances.map((u, i) => (
              <motion.div
                className={`utt${i === last ? " active" : ""}`}
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
          {state.partial ? (
            <motion.div className="utt" variants={item} initial="hidden" animate="show">
              <div className="utt-meta">
                <span className={`spk-tag ${state.partial.speaker}`}>{cap(state.partial.speaker)}</span>
              </div>
              <p className="utt-text">{state.partial.text}</p>
            </motion.div>
          ) : (
            state.utterances.length > 0 && (
              <div className="typing">
                <span />
                <span />
                <span />
              </div>
            )
          )}
        </div>

        <div className="knowledge-strip">
          <motion.div
            className="dropzone"
            onClick={() => fileRef.current?.click()}
            whileHover={{ scale: 1.01 }}
            whileTap={{ scale: 0.99 }}
          >
            <Icon name="upload_file" size={18} />
            Drop documents to add to Relay&apos;s knowledge
          </motion.div>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.txt"
            hidden
            onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0])}
          />
          <button className="coming-soon" disabled>
            <Icon name="cloud_off" size={16} />
            Connect Google Drive — coming soon
          </button>
        </div>
      </motion.section>

      {/* Right: suggested answers */}
      <motion.section
        className="col-right"
        initial={{ opacity: 0, x: 22 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.5, ease: easeOut, delay: 0.08 }}
      >
        <div className="section-label label-caps">
          <Icon name="auto_awesome" size={16} fill />
          Suggested Answers
        </div>
        <div className="cards-scroll scroll">
          {state.cards.length === 0 ? (
            <motion.div className="empty-state" variants={fadeUp} initial="hidden" animate="show">
              <Icon name="bolt" size={40} />
              <div className="big">Answers appear here, instantly</div>
              <div className="small">
                Every card is pulled from your documents and cited — in under half a second. The model
                retrieves, it doesn&apos;t guess.
              </div>
            </motion.div>
          ) : (
            <AnimatePresence>
              {state.cards.map((c, i) => (
                <RelayCard key={c.card_id} card={c} featured={i === 0} />
              ))}
            </AnimatePresence>
          )}
        </div>
      </motion.section>
    </div>
  );
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

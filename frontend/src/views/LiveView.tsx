import { useEffect, useRef } from "react";
import { AnimatePresence } from "framer-motion";
import type { RelaySessionState } from "../hooks/useRelaySession";
import { api } from "../api/client";
import { useBackend } from "../backend";
import { Waveform } from "../components/Waveform";
import { RelayCard } from "../components/RelayCard";
import { Icon } from "../components/Icon";
import { clock } from "../util";

interface Props {
  state: RelaySessionState;
}

export function LiveView({ state }: Props) {
  const { call } = useBackend();
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const last = state.utterances.length - 1;

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
      <section className="col-left card-surface callpanel">
        <div className="callpanel-head">
          <div className="callpanel-head-row">
            <h2 className="label-caps">Live Call</h2>
            <div className="call-status">
              <span className="mono call-timer">{new Date().toTimeString().slice(0, 5)}</span>
              <button className="mic-btn" title="Join / toggle microphone" onClick={onMic}>
                <Icon name="mic" size={18} />
                <span className="mic-dot" />
              </button>
            </div>
          </div>
          <Waveform active={!!state.partial} />
        </div>

        <div className="transcript scroll" ref={scrollRef}>
          {state.utterances.length === 0 && !state.partial && (
            <div className="empty-state" style={{ padding: 24 }}>
              <Icon name="hearing" size={32} />
              <div className="small">Listening for the conversation. Speak a question — or replay the demo beats.</div>
            </div>
          )}
          {state.utterances.map((u, i) => (
            <div className={`utt${i === last ? " active" : ""}`} key={u.utterance_id}>
              <div className="utt-meta">
                <span className={`spk-tag ${u.speaker}`}>{cap(u.speaker)}</span>
                <span className="utt-time">{clock(u.ts)}</span>
              </div>
              <p className="utt-text">{u.text}</p>
            </div>
          ))}
          {state.partial ? (
            <div className="utt">
              <div className="utt-meta">
                <span className={`spk-tag ${state.partial.speaker}`}>{cap(state.partial.speaker)}</span>
              </div>
              <p className="utt-text">{state.partial.text}</p>
            </div>
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
          <div className="dropzone" onClick={() => fileRef.current?.click()}>
            <Icon name="upload_file" size={18} />
            Drop documents to add to Relay&apos;s knowledge
          </div>
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
      </section>

      {/* Right: suggested answers */}
      <section className="col-right">
        <div className="section-label label-caps">
          <Icon name="auto_awesome" size={16} fill />
          Suggested Answers
        </div>
        <div className="cards-scroll scroll">
          {state.cards.length === 0 ? (
            <div className="empty-state">
              <Icon name="bolt" size={40} />
              <div className="big">Answers appear here, instantly</div>
              <div className="small">
                Every card is pulled from your documents and cited — in under half a second. The model
                retrieves, it doesn&apos;t guess.
              </div>
            </div>
          ) : (
            <AnimatePresence>
              {state.cards.map((c, i) => (
                <RelayCard key={c.card_id} card={c} featured={i === 0} />
              ))}
            </AnimatePresence>
          )}
        </div>
      </section>
    </div>
  );
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { RelaySessionState } from "../hooks/useRelaySession";
import { api } from "../api/client";
import { useBackend } from "../backend";
import { DESK_CUSTOMER } from "../mock/dataset";
import { Icon } from "../components/Icon";
import { initials } from "../util";
import { easeOut, fadeUp, iconHover, inView, pressable } from "../motion";

interface Props {
  state: RelaySessionState;
  onQuery: (text: string) => void;
}

export function DeskView({ state, onQuery }: Props) {
  const { call } = useBackend();
  const [text, setText] = useState("");
  const [editing, setEditing] = useState(false);
  const [reply, setReply] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const resolution = state.cards[0];

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [state.utterances, state.partial]);

  // Keep the editable reply in sync with the latest suggested resolution.
  useEffect(() => {
    if (resolution) setReply(resolution.answer);
  }, [resolution?.card_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = () => {
    const t = text.trim();
    if (!t) return;
    onQuery(t);
    setText("");
  };

  const onAttach = (file: File) =>
    call("Attach document", () => api.uploadDocument(file, file.name), {
      endpoint: "POST /documents",
      success: `Attached ${file.name}`,
    });

  const onSendReply = async () => {
    if (!state.sessionId || !resolution) return;
    await call("Send reply", () => api.sendReply(state.sessionId!, { card_id: resolution.card_id, text: reply }), {
      endpoint: `POST /sessions/${state.sessionId}/reply`,
      success: "Reply sent to customer",
    });
    setEditing(false);
  };

  const c = DESK_CUSTOMER;

  return (
    <div className="split">
      {/* Left: conversation */}
      <motion.section
        className="col-left card-surface callpanel"
        initial={{ opacity: 0, x: -22 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.5, ease: easeOut }}
      >
        <div className="callpanel-head">
          <div className="callpanel-head-row">
            <h2 className="label-caps">Conversation</h2>
          </div>
        </div>

        <div className="transcript scroll" ref={scrollRef} style={{ gap: 12 }}>
          {state.utterances.length === 0 && !state.partial && (
            <motion.div className="empty-state" style={{ padding: 24 }} variants={fadeUp} initial="hidden" animate="show">
              <Icon name="forum" size={32} />
              <div className="small">A customer message pulls the right doc + their history into a resolution.</div>
            </motion.div>
          )}
          <AnimatePresence initial={false}>
            {state.utterances.map((u) => (
              <ChatBubble key={u.utterance_id} speaker={u.speaker} text={u.text} name={c.name} />
            ))}
          </AnimatePresence>
          {state.partial && <ChatBubble speaker={state.partial.speaker} text={state.partial.text} name={c.name} />}
        </div>

        <div className="composer">
          <div className="composer-box">
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              placeholder="Type a message…"
            />
            <motion.button
              className="composer-attach"
              title="Attach document"
              onClick={() => fileRef.current?.click()}
              {...iconHover}
            >
              <Icon name="attach_file" size={20} />
            </motion.button>
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.docx,.txt"
              hidden
              onChange={(e) => e.target.files?.[0] && onAttach(e.target.files[0])}
            />
            <motion.button className="composer-send" onClick={submit} title="Send" {...pressable}>
              <Icon name="send" size={18} />
            </motion.button>
          </div>
        </div>
      </motion.section>

      {/* Right: customer + resolution */}
      <motion.section
        className="col-right"
        initial={{ opacity: 0, x: 22 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.5, ease: easeOut, delay: 0.08 }}
      >
        <div className="stack scroll">
          <motion.div className="card-surface customer-card" variants={fadeUp} initial="hidden" animate="show">
            <div className="section-label label-caps" style={{ paddingBottom: 16 }}>
              Customer
            </div>
            <div className="customer-head">
              <div className="customer-avatar">{initials(c.name)}</div>
              <div>
                <div className="customer-name">
                  <h3>{c.name}</h3>
                  <span className="plan-badge">
                    <span className="dot" />
                    {c.plan}
                  </span>
                </div>
                <div className="customer-co">{c.company}</div>
              </div>
            </div>

            <div className="tickets-label label-caps">Recent Tickets</div>
            {c.tickets.map((t, i) => (
              <motion.div
                className="ticket"
                key={t.title}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.35, ease: easeOut, delay: 0.2 + i * 0.08 }}
              >
                <Icon name="task_alt" size={18} fill />
                <span className="ticket-title">{t.title}</span>
                <span className="ticket-meta mono">{t.meta}</span>
              </motion.div>
            ))}
          </motion.div>

          {resolution ? (
            <motion.div
              className="card-surface resolution-card"
              variants={fadeUp}
              initial="hidden"
              whileInView="show"
              viewport={inView}
            >
              <div className="resolution-head">
                <span className="label-caps">
                  <Icon name="auto_awesome" size={16} fill />
                  Suggested Resolution
                </span>
                <span className="verified">
                  <Icon name="check_circle" size={14} fill />
                  Verified · {resolution.latency_ms}ms
                </span>
              </div>
              {editing ? (
                <textarea
                  className="resolution-edit"
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  rows={3}
                />
              ) : (
                <p className="resolution-quote">&ldquo;{reply}&rdquo;</p>
              )}
              <div className="tickets-label label-caps">Sources</div>
              <div className="source-list resolution-sources">
                {resolution.sources.map((s) => (
                  <motion.span
                    className="source-chip"
                    key={s.document_id}
                    whileHover={{ scale: 1.03 }}
                    whileTap={{ scale: 0.97 }}
                  >
                    <Icon name="description" size={14} />
                    {s.title}
                  </motion.span>
                ))}
              </div>
              <div className="resolution-actions">
                <motion.button className="btn-primary" onClick={onSendReply} {...pressable}>
                  <Icon name="send" size={16} />
                  Send reply
                </motion.button>
                <motion.button className="btn-secondary" onClick={() => setEditing((v) => !v)} {...pressable}>
                  {editing ? "Done" : "Edit"}
                </motion.button>
              </div>
            </motion.div>
          ) : (
            <div className="card-surface resolution-card">
              <div className="empty-state" style={{ padding: 16 }}>
                <Icon name="lightbulb" size={32} />
                <div className="small">The resolution appears once the customer describes the issue.</div>
              </div>
            </div>
          )}
        </div>
      </motion.section>
    </div>
  );
}

function ChatBubble({ speaker, text, name }: { speaker: string; text: string; name: string }) {
  const isRep = speaker === "rep" || speaker === "relay";
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 12, x: isRep ? 14 : -14 }}
      animate={{ opacity: 1, y: 0, x: 0 }}
      exit={{ opacity: 0, scale: 0.96 }}
      transition={{ type: "spring", stiffness: 360, damping: 28 }}
      style={{
        display: "flex",
        gap: 10,
        flexDirection: isRep ? "row-reverse" : "row",
        alignItems: "flex-start",
      }}
    >
      {!isRep && <div className="customer-avatar" style={{ width: 32, height: 32, fontSize: 12 }}>{initials(name)}</div>}
      <div
        style={{
          maxWidth: "78%",
          fontSize: 14,
          lineHeight: "20px",
          padding: "10px 14px",
          borderRadius: 12,
          background: isRep ? "var(--surface-container-lowest)" : "var(--surface-container)",
          border: isRep ? "1px solid var(--primary-container)" : "1px solid transparent",
          color: "var(--on-surface)",
        }}
      >
        {text}
      </div>
    </motion.div>
  );
}

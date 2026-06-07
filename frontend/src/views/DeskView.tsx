import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { RelaySessionState } from "../hooks/useRelaySession";
import { api } from "../api/client";
import { useBackend } from "../backend";
import { Icon } from "../components/Icon";
import { initials } from "../util";
import { easeOut, fadeUp, iconHover, inView, item, pressable, staggerParent } from "../motion";
import type { CustomerProfile } from "../types";

interface Props {
  state: RelaySessionState;
  // Send a reply to the CUSTOMER (delivers to their widget + shows on the rep side).
  onReply: (text: string, opts?: { card_id?: string }) => void | Promise<void>;
}

export function DeskView({ state, onReply }: Props) {
  const { call } = useBackend();
  const [text, setText] = useState("");
  const [editing, setEditing] = useState(false);
  const [reply, setReply] = useState("");
  const [customer, setCustomer] = useState<CustomerProfile | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const resolution = state.cards[0];

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [state.utterances, state.partial]);

  // Link the seeded customer (Sarah Chen / Acme Corp) to this Desk session so the
  // CUSTOMER panel populates and queries can pull her history/memory.
  useEffect(() => {
    let alive = true;
    api
      .listCustomers()
      .then((res) => {
        if (alive) setCustomer(res.customers[0] ?? null);
      })
      .catch(() => {
        /* no customer seeded — panel falls back to the empty state */
      });
    return () => {
      alive = false;
    };
  }, []);

  // Keep the editable reply in sync with the suggested resolution AS IT STREAMS IN.
  // The card streams token-by-token (card.new → many card.update with the SAME card_id),
  // so syncing on card_id alone froze `reply` at the first token ("I'"). Track the
  // answer itself — but never clobber the rep's manual edits while they're editing.
  useEffect(() => {
    if (resolution && !editing) setReply(resolution.answer);
  }, [resolution?.answer, editing]); // eslint-disable-line react-hooks/exhaustive-deps

  // The bottom composer is the rep REPLYING to the customer (not a co-pilot query):
  // it delivers to the customer's widget and stays visible in the conversation.
  const submit = () => {
    const t = text.trim();
    if (!t) return;
    void onReply(t);
    setText("");
  };

  const onAttach = (file: File) =>
    call("Attach document", () => api.uploadDocument(file, file.name), {
      endpoint: "POST /documents",
      success: `Attached ${file.name}`,
    });

  const onSendReply = async () => {
    if (!state.sessionId || !resolution || !reply.trim()) return;
    // Same path as the composer: deliver to the customer's widget + show on the rep side.
    await onReply(reply, { card_id: resolution.card_id });
    setEditing(false);
  };

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
            <RoutingBadge routing={state.routing} />
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
              <ChatBubble key={u.utterance_id} speaker={u.speaker} text={u.text} />
            ))}
          </AnimatePresence>
          {state.partial && <ChatBubble speaker={state.partial.speaker} text={state.partial.text} />}
        </div>

        <div className="composer">
          <div className="composer-box">
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              placeholder="Type your reply to the customer…"
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
            {customer ? (
              <>
                <div className="lead-head" style={{ paddingBottom: 4 }}>
                  <div className="lead-avatar">{initials(customer.name)}</div>
                  <div className="lead-id">
                    <h3>{customer.name}</h3>
                    <div className="sub">
                      {customer.company}
                      {customer.plan ? ` · ${customer.plan} plan` : ""}
                    </div>
                  </div>
                </div>
                <div className="tickets-label label-caps" style={{ marginTop: 12 }}>
                  Recent tickets
                </div>
                {customer.history.length === 0 ? (
                  <div className="small" style={{ paddingTop: 6 }}>No past tickets.</div>
                ) : (
                  <motion.div variants={staggerParent(0.06)} initial="hidden" animate="show">
                    {customer.history.map((h) => (
                      <motion.div className="ticket-row" key={h.memory_id} variants={item}>
                        <span className={`ticket-status ${h.resolved ? "resolved" : "open"}`}>
                          <Icon name={h.resolved ? "check_circle" : "schedule"} size={14} fill={h.resolved} />
                          {h.resolved ? "Resolved" : "Open"}
                        </span>
                        <span className="ticket-text">{h.text}</span>
                      </motion.div>
                    ))}
                  </motion.div>
                )}
              </>
            ) : (
              <div className="empty-state" style={{ padding: 16 }}>
                <Icon name="person" size={32} />
                <div className="small">
                  No customer linked to this session. Their profile &amp; recent tickets appear here once a
                  customer is matched.
                </div>
              </div>
            )}
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

// Routing badge — shows the classified department once the inbound classifier
// reports it on session.status. Hidden until routing is present.
const _DEPT_LABEL: Record<string, string> = {
  support: "Customer Support",
  sales: "Sales",
  it: "IT",
  // legacy values, just in case
  desk: "Customer Support",
  intake: "Sales",
};

export function RoutingBadge({
  routing,
}: {
  routing: { department: string; label?: string; confidence?: number } | null;
}) {
  if (!routing) return null;
  const label = routing.label ?? _DEPT_LABEL[routing.department] ?? routing.department;
  return (
    <motion.span
      className="label-caps"
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: easeOut }}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "4px 10px",
        borderRadius: 999,
        background: "var(--surface-container)",
        border: "1px solid var(--primary-container)",
        color: "var(--primary)",
        fontSize: 11,
      }}
      title={
        routing.confidence != null
          ? `Routed to ${label} · ${Math.round(routing.confidence * 100)}% confidence`
          : `Routed to ${label}`
      }
    >
      <Icon name="alt_route" size={14} fill />
      {label}
      {routing.confidence != null && (
        <span style={{ opacity: 0.65 }}>{Math.round(routing.confidence * 100)}%</span>
      )}
    </motion.span>
  );
}

function ChatBubble({ speaker, text }: { speaker: string; text: string }) {
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
      {!isRep && (
        <div className="customer-avatar" style={{ width: 32, height: 32 }}>
          <Icon name="person" size={18} />
        </div>
      )}
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

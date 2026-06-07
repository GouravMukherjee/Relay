import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { connectWidgetSocket, createThread, postMessage, type WidgetSocket } from "./client";
import type { ChatMessage, Department, WsInboundEvent } from "./types";

const DEPT_LABEL: Record<Department, string> = {
  desk: "Support",
  intake: "Sales",
};

const SUGGESTIONS = [
  "What's your return policy?",
  "How do I track my order?",
  "I'd like a quote for my team",
];

function fmtTime(ts: number): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

let _id = 0;
const nextId = () => `m${Date.now()}-${_id++}`;

export function ChatWidget({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [connected, setConnected] = useState(false);
  const [agentTyping, setAgentTyping] = useState(false);
  const [routedTo, setRoutedTo] = useState<Department | null>(null);

  const threadRef = useRef<string | null>(null);
  const socketRef = useRef<WidgetSocket | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Texts we've shown optimistically — used to dedupe the server's echo.
  const pendingEchoes = useRef<string[]>([]);

  const handleEvent = useCallback((ev: WsInboundEvent) => {
    if (ev.type === "status") {
      setAgentTyping(Boolean(ev.data.agent_typing));
      if (ev.data.routed_to) setRoutedTo(ev.data.routed_to);
      return;
    }
    // type === "message"
    const { role, text } = ev.data;
    const ts = ev.ts ? Date.parse(ev.ts) || Date.now() : Date.now();

    if (role === "customer") {
      // Dedupe our own optimistic message against the server echo.
      const idx = pendingEchoes.current.indexOf(text);
      if (idx !== -1) {
        pendingEchoes.current.splice(idx, 1);
        setMessages((prev) => {
          const i = prev.findIndex((m) => m.optimistic && m.role === "customer" && m.text === text);
          if (i === -1) return prev;
          const copy = prev.slice();
          copy[i] = { ...copy[i], optimistic: false, ts };
          return copy;
        });
        return;
      }
    } else {
      // An agent reply arrived — stop the typing indicator.
      setAgentTyping(false);
    }
    setMessages((prev) => [...prev, { id: nextId(), role, text, ts }]);
  }, []);

  // Open a thread + socket the first time the widget is opened.
  useEffect(() => {
    if (!open || threadRef.current) return;
    let alive = true;
    (async () => {
      try {
        const { thread_id, ws_url } = await createThread();
        if (!alive) return;
        threadRef.current = thread_id;
        socketRef.current = connectWidgetSocket(ws_url, {
          onEvent: handleEvent,
          onOpen: () => setConnected(true),
          onClose: () => setConnected(false),
        });
      } catch {
        /* backend offline — widget still renders, send will surface nothing */
      }
    })();
    return () => {
      alive = false;
    };
  }, [open, handleEvent]);

  // Tear the socket down when the component unmounts.
  useEffect(() => {
    return () => socketRef.current?.close();
  }, []);

  // Auto-scroll to newest.
  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages, agentTyping]);

  // Focus the input when the panel opens.
  useEffect(() => {
    if (open) requestAnimationFrame(() => textareaRef.current?.focus());
  }, [open]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text) return;
    setDraft("");

    // Optimistic render + remember for echo dedupe.
    pendingEchoes.current.push(text);
    setMessages((prev) => [...prev, { id: nextId(), role: "customer", text, ts: Date.now(), optimistic: true }]);

    const thread = threadRef.current;
    // Primary send-path: over the WebSocket. Fall back to REST if not open.
    const sentOverWs = socketRef.current?.send(text) ?? false;
    if (!sentOverWs && thread) {
      try {
        await postMessage(thread, text);
      } catch {
        /* leave the optimistic bubble; server echo will reconcile on reconnect */
      }
    }
  }, [draft]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  const onDraftChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setDraft(e.target.value);
    const el = e.target;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 96)}px`;
  };

  const pickSuggestion = (s: string) => {
    setDraft(s);
    requestAnimationFrame(() => textareaRef.current?.focus());
  };

  const statusText = useMemo(() => {
    if (agentTyping) return "Agent is typing…";
    return connected ? "We typically reply in a few seconds" : "Connecting…";
  }, [agentTyping, connected]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="nw-chat"
          role="dialog"
          aria-label="Northwind support chat"
          initial={{ opacity: 0, y: 24, scale: 0.96 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 24, scale: 0.96 }}
          transition={{ type: "spring", stiffness: 360, damping: 30 }}
        >
          {/* Header */}
          <div className="nw-chat-head">
            <div className="nw-chat-head-row">
              <div className="nw-chat-avatar">
                <span className="nw-ms">support_agent</span>
                <span className="nw-presence" />
              </div>
              <div className="nw-chat-title">
                <h2>Northwind Support</h2>
                <div className="nw-chat-status">
                  <span className="nw-dot" />
                  {statusText}
                </div>
              </div>
              <button className="nw-chat-close" onClick={onClose} aria-label="Close chat">
                <span className="nw-ms">close</span>
              </button>
            </div>
            <AnimatePresence>
              {routedTo && (
                <motion.div
                  className="nw-routed"
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                >
                  <span className="nw-ms">alternate_email</span>
                  Connected to the {DEPT_LABEL[routedTo]} team
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Messages */}
          <div className="nw-body" ref={bodyRef}>
            {messages.length === 0 && !agentTyping ? (
              <div className="nw-empty">
                <div className="nw-empty-ic">
                  <span className="nw-ms">waving_hand</span>
                </div>
                <h3>How can we help?</h3>
                <p>Ask us anything — our team is here and ready to get you sorted.</p>
                <div className="nw-suggestions">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} className="nw-suggestion" onClick={() => pickSuggestion(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <AnimatePresence initial={false}>
                {messages.map((m) => (
                  <motion.div
                    key={m.id}
                    layout
                    className={`nw-msg ${m.role}`}
                    initial={{ opacity: 0, y: 10, scale: 0.98 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    transition={{ type: "spring", stiffness: 420, damping: 32 }}
                  >
                    <div className={`nw-avatar ${m.role}`}>
                      {m.role === "agent" ? <span className="nw-ms">support_agent</span> : "You"}
                    </div>
                    <div className="nw-bubble-wrap">
                      <div className={`nw-bubble${m.optimistic ? " optimistic" : ""}`}>{m.text}</div>
                      <span className="nw-time">{fmtTime(m.ts)}</span>
                    </div>
                  </motion.div>
                ))}
              </AnimatePresence>
            )}

            <AnimatePresence>
              {agentTyping && (
                <motion.div
                  className="nw-typing"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                >
                  <div className="nw-avatar agent">
                    <span className="nw-ms">support_agent</span>
                  </div>
                  <div className="nw-typing-bubble">
                    <span />
                    <span />
                    <span />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Composer */}
          <div className="nw-composer">
            <div className="nw-composer-box">
              <textarea
                ref={textareaRef}
                rows={1}
                value={draft}
                onChange={onDraftChange}
                onKeyDown={onKeyDown}
                placeholder="Type your message…"
              />
              <button className="nw-send" onClick={() => void send()} disabled={!draft.trim()} aria-label="Send message">
                <span className="nw-ms">send</span>
              </button>
            </div>
            <div className="nw-footnote">
              Powered by <b>Relay</b> · grounded answers from Northwind&apos;s own docs
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

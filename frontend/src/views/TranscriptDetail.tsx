import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Icon } from "../components/Icon";
import { RelayCard } from "../components/RelayCard";
import { easeOut, item, staggerParent } from "../motion";
import type { Card, SessionInfo, Utterance } from "../types";

const MODE_ICON: Record<string, string> = {
  live: "graphic_eq",
  desk: "support_agent",
  intake: "person_search",
};

const MODE_LABEL: Record<string, string> = {
  live: "Live session",
  desk: "Desk session",
  intake: "Intake session",
};

// Friendly, human-readable label for a session: "Live session · Jun 7, 3:24 PM".
export function sessionLabel(s: SessionInfo): string {
  const mode = MODE_LABEL[s.mode] ?? `${s.mode} session`;
  return `${mode} · ${formatWhen(s.started_at)}`;
}

export function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

interface Props {
  session: SessionInfo | null;
  onClose: () => void;
}

export function TranscriptDetail({ session, onClose }: Props) {
  const [utterances, setUtterances] = useState<Utterance[] | null>(null);
  const [cards, setCards] = useState<Card[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Close on Escape.
  useEffect(() => {
    if (!session) return;
    const handler = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [session, onClose]);

  // Load transcript + cards whenever a session is opened.
  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    const id = session.session_id;

    setLoading(true);
    setError(null);
    setUtterances(null);
    setCards(null);

    Promise.all([api.getTranscript(id), api.getCards(id)])
      .then(([t, c]) => {
        if (cancelled) return;
        setUtterances(t.utterances ?? []);
        setCards(c.cards ?? []);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const msg =
          e && typeof e === "object" && "message" in e
            ? String((e as { message?: unknown }).message)
            : "Something went wrong";
        setError(msg);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [session]);

  return (
    <AnimatePresence>
      {session && (
        <>
          <motion.div
            className="modal-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
            onClick={onClose}
          />

          <motion.div
            className="transcript-panel"
            initial={{ opacity: 0, x: 40 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 40 }}
            transition={{ duration: 0.22, ease: easeOut }}
            role="dialog"
            aria-modal="true"
            aria-label="Session transcript"
          >
            <div className="transcript-header">
              <div className="transcript-head-main">
                <span className={`mode-chip ${session.mode}`}>
                  <Icon name={MODE_ICON[session.mode] ?? "graphic_eq"} size={16} />
                  {session.mode}
                </span>
                <h2 className="transcript-title">{sessionLabel(session)}</h2>
                <span className="transcript-id mono">{session.session_id}</span>
              </div>
              <button className="settings-close" onClick={onClose} aria-label="Close transcript">
                <Icon name="close" size={20} />
              </button>
            </div>

            <div className="transcript-body scroll">
              {loading && <div className="page-empty">Loading transcript…</div>}
              {error && <div className="page-empty error">Couldn’t load transcript — {error}</div>}

              {!loading && !error && (
                <>
                  {/* Cards produced */}
                  <section className="transcript-section">
                    <h3 className="transcript-section-title label-caps">
                      Cards produced
                      <span className="transcript-count">{cards?.length ?? 0}</span>
                    </h3>
                    {cards && cards.length > 0 ? (
                      <motion.div
                        className="transcript-cards"
                        variants={staggerParent(0.05)}
                        initial="hidden"
                        animate="show"
                      >
                        {cards.map((c) => (
                          <motion.div key={c.card_id} variants={item}>
                            <RelayCard card={c} />
                          </motion.div>
                        ))}
                      </motion.div>
                    ) : (
                      <div className="transcript-empty">No cards were surfaced in this session.</div>
                    )}
                  </section>

                  {/* Transcript */}
                  <section className="transcript-section">
                    <h3 className="transcript-section-title label-caps">
                      Transcript
                      <span className="transcript-count">{utterances?.length ?? 0}</span>
                    </h3>
                    {utterances && utterances.length > 0 ? (
                      <motion.ol
                        className="transcript-lines"
                        variants={staggerParent(0.025)}
                        initial="hidden"
                        animate="show"
                      >
                        {utterances.map((u) => (
                          <motion.li
                            key={u.utterance_id}
                            className={`utterance speaker-${u.speaker}`}
                            variants={item}
                          >
                            <div className="utterance-meta">
                              <span className="utterance-speaker">{u.speaker}</span>
                              <span className="utterance-ts mono">{formatTs(u.ts)}</span>
                            </div>
                            <p className="utterance-text">{u.text}</p>
                          </motion.li>
                        ))}
                      </motion.ol>
                    ) : (
                      <div className="transcript-empty">No transcript captured for this session.</div>
                    )}
                  </section>
                </>
              )}
            </div>
          </motion.div>

          {/* Scoped styles — global.css is owned elsewhere; these match its tokens. */}
          <style>{PANEL_CSS}</style>
        </>
      )}
    </AnimatePresence>
  );
}

function formatTs(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

const PANEL_CSS = `
.transcript-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(560px, 96vw);
  background: var(--surface);
  border-left: 1px solid var(--outline-variant);
  box-shadow: -8px 0 40px rgba(0, 0, 0, 0.12);
  display: flex;
  flex-direction: column;
  z-index: 201;
}
.transcript-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 24px 16px;
  border-bottom: 1px solid var(--outline-variant);
  flex-shrink: 0;
}
.transcript-head-main {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
}
.transcript-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--on-surface);
}
.transcript-id {
  color: var(--on-surface-variant);
  font-size: 12px;
}
.transcript-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px 40px;
  display: flex;
  flex-direction: column;
  gap: 28px;
}
.transcript-section {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.transcript-section-title {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0;
  color: var(--on-surface-variant);
  letter-spacing: 0.08em;
}
.transcript-count {
  display: inline-grid;
  place-items: center;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  border-radius: var(--r-full);
  background: var(--surface-container);
  color: var(--on-surface-variant);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0;
}
.transcript-cards {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.transcript-empty {
  padding: 18px;
  text-align: center;
  font-size: 13px;
  color: var(--on-surface-variant);
  background: var(--surface-container-low);
  border: 1px dashed var(--outline-variant);
  border-radius: var(--r-xl);
}
.transcript-lines {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.utterance {
  padding-left: 14px;
  border-left: 2px solid var(--surface-variant);
}
.utterance.speaker-rep,
.utterance.speaker-agent,
.utterance.speaker-assistant {
  border-left-color: var(--primary-container);
}
.utterance.speaker-prospect,
.utterance.speaker-customer,
.utterance.speaker-caller {
  border-left-color: var(--emerald-500);
}
.utterance-meta {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 4px;
}
.utterance-speaker {
  font-size: 12px;
  font-weight: 600;
  text-transform: capitalize;
  color: var(--on-surface);
}
.utterance-ts {
  font-size: 11px;
  color: var(--outline);
}
.utterance-text {
  margin: 0;
  font-size: 14px;
  line-height: 1.55;
  color: var(--on-surface);
}
`;

import { motion } from "framer-motion";
import { api } from "../api/client";
import { useResource } from "../hooks/useResource";
import { Icon } from "../components/Icon";
import { fadeUp, hoverCard, inView, item, staggerParent } from "../motion";

const MODE_ICON: Record<string, string> = { live: "graphic_eq", desk: "support_agent", intake: "person_search" };

export function TranscriptsView() {
  const { data, loading, error } = useResource(() => api.listSessions().then((r) => r.sessions));

  return (
    <div className="section-page">
      <motion.div className="section-page-head" variants={fadeUp} initial="hidden" animate="show">
        <div>
          <h1 className="page-title">Transcripts</h1>
          <p className="page-sub">Past sessions and the cards they produced.</p>
        </div>
      </motion.div>

      {loading && <div className="page-empty">Loading sessions…</div>}
      {error && <div className="page-empty error">Couldn’t load sessions — {error}</div>}
      {data && data.length === 0 && <div className="page-empty">No sessions yet.</div>}

      {data && (
        <motion.div
          className="session-grid"
          variants={staggerParent(0.06)}
          initial="hidden"
          whileInView="show"
          viewport={inView}
        >
          {data.map((s) => (
            <motion.button className="card-surface session-card" key={s.session_id} variants={item} {...hoverCard}>
              <div className="session-card-top">
                <span className={`mode-chip ${s.mode}`}>
                  <Icon name={MODE_ICON[s.mode] ?? "graphic_eq"} size={16} />
                  {s.mode}
                </span>
                <span className={`status-dot ${s.status === "ended" ? "ready" : "processing"}`} />
              </div>
              <div className="session-id mono">{s.session_id}</div>
              <div className="session-meta">
                <span>
                  <b>{s.card_count}</b> cards
                </span>
                <span className="mono">{new Date(s.started_at).toLocaleString()}</span>
              </div>
            </motion.button>
          ))}
        </motion.div>
      )}
    </div>
  );
}

import { api } from "../api/client";
import { useResource } from "../hooks/useResource";
import { DEMO_SESSIONS } from "../mock/dataset";
import { Icon } from "../components/Icon";
import { DemoBanner } from "./KnowledgeView";

const MODE_ICON: Record<string, string> = { live: "graphic_eq", desk: "support_agent", intake: "person_search" };

export function TranscriptsView() {
  const { data, loading, error, demo } = useResource(
    () => api.listSessions().then((r) => r.sessions),
    DEMO_SESSIONS,
  );

  return (
    <div className="section-page">
      <div className="section-page-head">
        <div>
          <h1 className="page-title">Transcripts</h1>
          <p className="page-sub">Past sessions and the cards they produced.</p>
        </div>
      </div>

      {demo && <DemoBanner endpoint="GET /sessions" />}
      {loading && <div className="page-empty">Loading sessions…</div>}
      {error && <div className="page-empty error">Couldn’t load sessions — {error}</div>}

      {data && (
        <div className="session-grid">
          {data.map((s) => (
            <button className="card-surface session-card" key={s.session_id}>
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
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

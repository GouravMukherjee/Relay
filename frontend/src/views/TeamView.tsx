import { api } from "../api/client";
import { useResource } from "../hooks/useResource";
import { DEMO_USERS } from "../mock/dataset";
import { initials } from "../util";
import { DemoBanner } from "./KnowledgeView";

export function TeamView() {
  const { data, loading, error, demo } = useResource(
    () => api.listUsers().then((r) => r.users),
    DEMO_USERS,
  );

  return (
    <div className="section-page">
      <div className="section-page-head">
        <div>
          <h1 className="page-title">Team</h1>
          <p className="page-sub">People in this Relay workspace.</p>
        </div>
      </div>

      {demo && <DemoBanner endpoint="GET /users" />}
      {loading && <div className="page-empty">Loading team…</div>}
      {error && <div className="page-empty error">Couldn’t load team — {error}</div>}

      {data && (
        <div className="team-grid">
          {data.map((u) => (
            <div className="card-surface team-card" key={u.id}>
              <div className="lead-avatar">{initials(u.name)}</div>
              <div>
                <div className="team-name">{u.name}</div>
                <div className="team-role">{u.role}</div>
                {u.email && <div className="team-email mono">{u.email}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

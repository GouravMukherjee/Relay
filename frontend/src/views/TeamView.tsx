import { motion } from "framer-motion";
import { api } from "../api/client";
import { useResource } from "../hooks/useResource";
import { initials } from "../util";
import { fadeUp, hoverCard, inView, item, staggerParent } from "../motion";

export function TeamView() {
  const { data, loading, error } = useResource(() => api.listUsers().then((r) => r.users));

  return (
    <div className="section-page">
      <motion.div className="section-page-head" variants={fadeUp} initial="hidden" animate="show">
        <div>
          <h1 className="page-title">Team</h1>
          <p className="page-sub">People in this Relay workspace.</p>
        </div>
      </motion.div>

      {loading && <div className="page-empty">Loading team…</div>}
      {error && <div className="page-empty error">Couldn’t load team — {error}</div>}
      {data && data.length === 0 && <div className="page-empty">No teammates yet.</div>}

      {data && (
        <motion.div
          className="team-grid"
          variants={staggerParent(0.06)}
          initial="hidden"
          whileInView="show"
          viewport={inView}
        >
          {data.map((u) => (
            <motion.div className="card-surface team-card" key={u.id} variants={item} {...hoverCard}>
              <div className="lead-avatar">{initials(u.name)}</div>
              <div>
                <div className="team-name">{u.name}</div>
                <div className="team-role">{u.role}</div>
                {u.email && <div className="team-email mono">{u.email}</div>}
              </div>
            </motion.div>
          ))}
        </motion.div>
      )}
    </div>
  );
}

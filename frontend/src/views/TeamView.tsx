import { motion } from "framer-motion";
import { api } from "../api/client";
import { useResource } from "../hooks/useResource";
import { initials } from "../util";
import { fadeUp, hoverCard, inView, item, staggerParent } from "../motion";
import type { User } from "../api/client";

// The Team page is not demo-critical, but it shouldn't render empty. A fresh workspace
// only has the single signed-in user, so when the API returns ≤1 real teammate (or
// errors) we show the mock Northwind team so the page always looks populated.
const NORTHWIND_TEAM: User[] = [
  { id: "usr_demo_1", name: "Sarah Chen", role: "Account Executive", email: "sarah.chen@northwind.example" },
  { id: "usr_demo_2", name: "Marcus Reed", role: "Solutions Engineer", email: "marcus.reed@northwind.example" },
  { id: "usr_demo_3", name: "Priya Nair", role: "Support Lead", email: "priya.nair@northwind.example" },
  { id: "usr_demo_4", name: "Diego Alvarez", role: "Sales Manager", email: "diego.alvarez@northwind.example" },
];

export function TeamView() {
  const { data, loading } = useResource(() => api.listUsers().then((r) => r.users));

  // Use real users when there's a real team; otherwise fall back to the mock roster.
  const team = data && data.length > 1 ? data : NORTHWIND_TEAM;
  const isMock = !(data && data.length > 1);

  return (
    <div className="section-page">
      <motion.div className="section-page-head" variants={fadeUp} initial="hidden" animate="show">
        <div>
          <h1 className="page-title">Team</h1>
          <p className="page-sub">
            People in this Relay workspace{isMock ? " · demo roster" : ""}.
          </p>
        </div>
      </motion.div>

      {loading && <div className="page-empty">Loading team…</div>}

      {!loading && (
        <motion.div
          className="team-grid"
          variants={staggerParent(0.06)}
          initial="hidden"
          whileInView="show"
          viewport={inView}
        >
          {team.map((u) => (
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

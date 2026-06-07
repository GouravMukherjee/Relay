import { motion } from "framer-motion";
import { BACKEND_HOST } from "../config";
import type { Mode } from "../types";
import { Icon } from "./Icon";
import { easeOut, itemLeft, pressable, staggerParent } from "../motion";

export type NavKey = "dashboard" | "transcripts" | "knowledge" | "team" | "account";

const NAV: { key: NavKey; icon: string; label: string }[] = [
  { key: "dashboard", icon: "dashboard", label: "Dashboard" },
  { key: "transcripts", icon: "chat", label: "Transcripts" },
  { key: "knowledge", icon: "menu_book", label: "Knowledge" },
  { key: "team", icon: "group", label: "Team" },
];

const NAV_BOTTOM: { key: NavKey; icon: string; label: string }[] = [
  { key: "account", icon: "account_circle", label: "Account" },
];

const TITLES: Record<Mode, string> = {
  live: "Relay Co-pilot",
  desk: "Relay Desk",
  intake: "Relay Intake",
};

interface Props {
  mode: Mode;
  nav: NavKey;
  onNav: (n: NavKey) => void;
  onNewAnalysis: () => void;
  status: "connecting" | "active" | "ended";
  collapsed: boolean;
}

export function Sidebar({ mode, nav, onNav, onNewAnalysis, status, collapsed }: Props) {
  return (
    <motion.aside
      className="sidebar"
      initial={{ x: -40, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      transition={{ duration: 0.5, ease: easeOut }}
    >
      <div className="sidebar-brand">
        <img className="brand-logo" src="/relay-logo.png" alt="Relay" width={32} height={32} />
        <span className="brand-name">Relay</span>
      </div>

      <div className="sidebar-head">
        <h2 className="label-caps">{TITLES[mode]}</h2>
        <motion.button
          className="btn-new"
          onClick={onNewAnalysis}
          title={collapsed ? "New Analysis" : undefined}
          {...pressable}
        >
          <Icon name="add" size={18} />
          <span>New Analysis</span>
        </motion.button>
      </div>

      <motion.nav className="sidebar-nav" variants={staggerParent(0.07, 0.15)} initial="hidden" animate="show">
        {NAV.map((entry) => (
          <motion.button
            key={entry.key}
            className={`side-link${nav === entry.key ? " active" : ""}`}
            onClick={() => onNav(entry.key)}
            title={collapsed ? entry.label : undefined}
            variants={itemLeft}
            whileHover={{ x: collapsed ? 0 : 4 }}
            whileTap={{ scale: 0.98 }}
          >
            <Icon name={entry.icon} size={20} fill={nav === entry.key} />
            <span>{entry.label}</span>
          </motion.button>
        ))}
      </motion.nav>

      <div className="sidebar-foot">
        {NAV_BOTTOM.map((entry) => (
          <motion.button
            key={entry.key}
            className={`side-link side-link-sm${nav === entry.key ? " active" : ""}`}
            onClick={() => onNav(entry.key)}
            title={collapsed ? entry.label : undefined}
            whileHover={{ x: collapsed ? 0 : 4 }}
            whileTap={{ scale: 0.98 }}
          >
            <Icon name={entry.icon} size={20} fill={nav === entry.key} />
            <span>{entry.label}</span>
          </motion.button>
        ))}
        <span className="conn-flag" title={`Backend · ${BACKEND_HOST}`}>
          <span className={`conn-dot ${status}`} />
          <span className="conn-label">
            {status === "active" ? "Online" : status === "connecting" ? "Connecting…" : "Offline"}
          </span>
        </span>
      </div>
    </motion.aside>
  );
}

import { motion } from "framer-motion";
import { USE_MOCK } from "../config";
import type { Mode } from "../types";
import { Icon } from "./Icon";
import { easeOut, itemLeft, pressable, staggerParent } from "../motion";

export type NavKey = "dashboard" | "transcripts" | "knowledge" | "team";

const NAV: { key: NavKey; icon: string; label: string }[] = [
  { key: "dashboard", icon: "dashboard", label: "Dashboard" },
  { key: "transcripts", icon: "chat", label: "Transcripts" },
  { key: "knowledge", icon: "menu_book", label: "Knowledge" },
  { key: "team", icon: "group", label: "Team" },
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
}

export function Sidebar({ mode, nav, onNav, onNewAnalysis }: Props) {
  return (
    <motion.aside
      className="sidebar"
      initial={{ x: -40, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      transition={{ duration: 0.5, ease: easeOut }}
    >
      <div className="sidebar-head">
        <h2 className="label-caps">{TITLES[mode]}</h2>
        <motion.button className="btn-new" onClick={onNewAnalysis} {...pressable}>
          <Icon name="add" size={18} />
          New Analysis
        </motion.button>
      </div>

      <motion.nav className="sidebar-nav" variants={staggerParent(0.07, 0.15)} initial="hidden" animate="show">
        {NAV.map((entry) => (
          <motion.button
            key={entry.key}
            className={`side-link${nav === entry.key ? " active" : ""}`}
            onClick={() => onNav(entry.key)}
            variants={itemLeft}
            whileHover={{ x: 4 }}
            whileTap={{ scale: 0.98 }}
          >
            <Icon name={entry.icon} size={20} fill={nav === entry.key} />
            {entry.label}
          </motion.button>
        ))}
      </motion.nav>

      {USE_MOCK && (
        <div className="sidebar-foot">
          <span className="demo-flag">
            <Icon name="bolt" size={13} fill />
            Demo engine
          </span>
        </div>
      )}
    </motion.aside>
  );
}

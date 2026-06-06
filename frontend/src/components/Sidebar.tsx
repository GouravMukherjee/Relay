import { USE_MOCK } from "../config";
import type { Mode } from "../types";
import { Icon } from "./Icon";

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
    <aside className="sidebar">
      <div className="sidebar-head">
        <h2 className="label-caps">{TITLES[mode]}</h2>
        <button className="btn-new" onClick={onNewAnalysis}>
          <Icon name="add" size={18} />
          New Analysis
        </button>
      </div>

      <nav className="sidebar-nav">
        {NAV.map((item) => (
          <button
            key={item.key}
            className={`side-link${nav === item.key ? " active" : ""}`}
            onClick={() => onNav(item.key)}
          >
            <Icon name={item.icon} size={20} fill={nav === item.key} />
            {item.label}
          </button>
        ))}
      </nav>

      {USE_MOCK && (
        <div className="sidebar-foot">
          <span className="demo-flag">
            <Icon name="bolt" size={13} fill />
            Demo engine
          </span>
        </div>
      )}
    </aside>
  );
}

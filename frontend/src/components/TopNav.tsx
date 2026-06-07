import { motion } from "framer-motion";
import type { Mode } from "../types";
import { api } from "../api/client";
import { useBackend } from "../backend";
import { Icon } from "./Icon";
import { AccountMenu } from "./AccountMenu";
import { easeOut, iconHover } from "../motion";

const MODES: { id: Mode; label: string }[] = [
  { id: "live", label: "Live" },
  { id: "desk", label: "Desk" },
  { id: "intake", label: "Intake" },
];

interface Props {
  mode: Mode;
  onMode: (m: Mode) => void;
  onSettings: () => void;
  onToggleSidebar: () => void;
  collapsed: boolean;
  email: string | null;
  onSignOut?: () => void;
}

export function TopNav({ mode, onMode, onSettings, onToggleSidebar, collapsed, email, onSignOut }: Props) {
  const { call } = useBackend();

  return (
    <motion.nav
      className="topnav"
      initial={{ y: -64, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.5, ease: easeOut }}
    >
      <div className="topnav-inner">
        <div className="topnav-left">
          <motion.button
            className="nav-icon sidebar-toggle"
            onClick={onToggleSidebar}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            {...iconHover}
          >
            <Icon name={collapsed ? "menu" : "menu_open"} size={22} />
          </motion.button>
        </div>

        <div className="modeswitch">
          {MODES.map((m) => (
            <motion.button
              key={m.id}
              className={`mode-link${mode === m.id ? " active" : ""}`}
              onClick={() => onMode(m.id)}
              whileHover={{ scale: mode === m.id ? 1 : 1.04 }}
              whileTap={{ scale: 0.96 }}
            >
              {m.id === "live" && mode === "live" && <span className="live-dot" />}
              {m.label}
            </motion.button>
          ))}
        </div>

        <div className="topnav-right">
          <motion.button
            className="nav-icon"
            title="Notifications"
            onClick={() =>
              call("Notifications", () => api.listNotifications(), { endpoint: "GET /notifications" })
            }
            {...iconHover}
          >
            <Icon name="notifications" size={22} />
          </motion.button>
          <motion.button className="nav-icon" title="Settings" onClick={onSettings} {...iconHover}>
            <Icon name="settings" size={22} />
          </motion.button>
          <AccountMenu email={email} onSignOut={onSignOut} />
        </div>
      </div>
    </motion.nav>
  );
}

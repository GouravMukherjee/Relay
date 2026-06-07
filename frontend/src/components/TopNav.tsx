import { motion } from "framer-motion";
import type { Mode } from "../types";
import { api } from "../api/client";
import { useBackend } from "../backend";
import { Icon } from "./Icon";
import { easeOut, iconHover, pressable } from "../motion";

const MODES: { id: Mode; label: string }[] = [
  { id: "live", label: "Live" },
  { id: "desk", label: "Desk" },
  { id: "intake", label: "Intake" },
];

interface Props {
  mode: Mode;
  onMode: (m: Mode) => void;
  onSettings: () => void;
}

export function TopNav({ mode, onMode, onSettings }: Props) {
  const { call } = useBackend();

  return (
    <motion.nav
      className="topnav"
      initial={{ y: -64, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.5, ease: easeOut }}
    >
      <div className="topnav-inner">
        <motion.div
          className="brand"
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.5, ease: easeOut, delay: 0.1 }}
        >
          <img className="brand-logo" src="/relay-logo.png" alt="Relay" width={32} height={32} />
          <span className="brand-name">Relay</span>
        </motion.div>

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
          <motion.button
            className="avatar"
            title="Account"
            onClick={() => call("Account", () => api.getMe(), { endpoint: "GET /me" })}
            {...pressable}
          >
            RA
          </motion.button>
        </div>
      </div>
    </motion.nav>
  );
}

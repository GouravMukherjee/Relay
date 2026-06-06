import type { Mode } from "../types";
import { api } from "../api/client";
import { useBackend } from "../backend";
import { Icon } from "./Icon";

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
    <nav className="topnav">
      <div className="topnav-inner">
        <div className="brand">Relay</div>

        <div className="modeswitch">
          {MODES.map((m) => (
            <button
              key={m.id}
              className={`mode-link${mode === m.id ? " active" : ""}`}
              onClick={() => onMode(m.id)}
            >
              {m.id === "live" && mode === "live" && <span className="live-dot" />}
              {m.label}
            </button>
          ))}
        </div>

        <div className="topnav-right">
          <button
            className="nav-icon"
            title="Notifications"
            onClick={() =>
              call("Notifications", () => api.listNotifications(), { endpoint: "GET /notifications" })
            }
          >
            <Icon name="notifications" size={22} />
          </button>
          <button className="nav-icon" title="Settings" onClick={onSettings}>
            <Icon name="settings" size={22} />
          </button>
          <button
            className="avatar"
            title="Account"
            onClick={() => call("Account", () => api.getMe(), { endpoint: "GET /me" })}
          >
            RA
          </button>
        </div>
      </div>
    </nav>
  );
}

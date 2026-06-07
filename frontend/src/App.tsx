import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useRelaySession } from "./hooks/useRelaySession";
import { useBackend } from "./backend";
import { easeOut } from "./motion";
import { TopNav } from "./components/TopNav";
import { Sidebar, type NavKey } from "./components/Sidebar";
import { SettingsModal } from "./components/SettingsModal";
import { LiveView } from "./views/LiveView";
import { DeskView } from "./views/DeskView";
import { IntakeView } from "./views/IntakeView";
import { KnowledgeView } from "./views/KnowledgeView";
import { TranscriptsView } from "./views/TranscriptsView";
import { TeamView } from "./views/TeamView";
import { AccountView } from "./views/AccountView";
import { AuthProvider } from "./auth/AuthContext";
import { LoginGate } from "./auth/LoginGate";
import { AuthBridge } from "./auth/AuthBridge";

interface Account {
  email: string | null;
  onSignOut: () => void | Promise<void>;
}

// ── Main dashboard ────────────────────────────────────────────────────────────

function Dashboard({ account }: { account?: Account }) {
  const { state, setMode, sendQuery, routeLead, toggleMic, restart, setLiveSource } =
    useRelaySession("live");
  const { toast } = useBackend();
  const [nav, setNav] = useState<NavKey>("dashboard");
  const [settingsOpen, setSettingsOpen] = useState(false);

  // Surface backend connection errors as a toast.
  const lastShownError = useRef<string | null>(null);
  useEffect(() => {
    if (state.lastError && state.lastError !== lastShownError.current) {
      lastShownError.current = state.lastError;
      toast(state.lastError, "error");
    }
  }, [state.lastError, toast]);

  const onMode = (m: typeof state.mode) => {
    setMode(m);
    setNav("dashboard");
  };

  // New Session: restart the live session IN PLACE (new room, cleared transcript +
  // cards + timer). Single-page — no new tab, the old session is torn down.
  const onNewAnalysis = () => {
    setNav("dashboard");
    restart();
    toast("New session started", "success");
  };

  // Sidebar collapse state, remembered across reloads.
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem("relay.sidebar") === "collapsed"
  );
  const toggleSidebar = () => {
    setCollapsed((c) => {
      const next = !c;
      localStorage.setItem("relay.sidebar", next ? "collapsed" : "expanded");
      return next;
    });
  };

  return (
    <div className={`app${collapsed ? " collapsed" : ""}`} data-mode={state.mode}>
      <Sidebar
        mode={state.mode}
        nav={nav}
        onNav={setNav}
        onNewAnalysis={onNewAnalysis}
        status={state.status}
        collapsed={collapsed}
      />

      <div className="app-main">
        <TopNav
          mode={state.mode}
          onMode={onMode}
          onSettings={() => setSettingsOpen(true)}
          onAccount={() => setNav("account")}
          onToggleSidebar={toggleSidebar}
          collapsed={collapsed}
          email={account?.email ?? null}
          onSignOut={account?.onSignOut}
        />

        <div className="workspace">
          <main className="main">
            <AnimatePresence mode="wait">
              <motion.div
                key={nav === "dashboard" ? `dash-${state.mode}` : nav}
                className="view-wrap"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.28, ease: easeOut }}
              >
                {nav === "dashboard" && state.mode === "live" && (
                  <LiveView
                    state={state}
                    onQuery={sendQuery}
                    onToggleMic={toggleMic}
                    onSetSource={setLiveSource}
                  />
                )}
                {nav === "dashboard" && state.mode === "desk" && <DeskView state={state} onQuery={sendQuery} />}
                {nav === "dashboard" && state.mode === "intake" && (
                  <IntakeView state={state} onRoute={routeLead} onQuery={sendQuery} />
                )}
                {nav === "knowledge" && <KnowledgeView />}
                {nav === "transcripts" && <TranscriptsView />}
                {nav === "team" && <TeamView />}
                {nav === "account" && (
                  <AccountView email={account?.email ?? null} onSignOut={account?.onSignOut} />
                )}
              </motion.div>
            </AnimatePresence>
          </main>
        </div>
      </div>

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}

// ── Root ──────────────────────────────────────────────────────────────────────

export function App() {
  return (
    <AuthProvider>
      <LoginGate>
        <AuthBridge>{(account) => <Dashboard account={account} />}</AuthBridge>
      </LoginGate>
    </AuthProvider>
  );
}

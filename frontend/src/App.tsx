import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useRelaySession } from "./hooks/useRelaySession";
import { useBackend } from "./backend";
import { api } from "./api/client";
import { easeOut } from "./motion";
import { TopNav } from "./components/TopNav";
import { Sidebar, type NavKey } from "./components/Sidebar";
import { LiveView } from "./views/LiveView";
import { DeskView } from "./views/DeskView";
import { IntakeView } from "./views/IntakeView";
import { KnowledgeView } from "./views/KnowledgeView";
import { TranscriptsView } from "./views/TranscriptsView";
import { TeamView } from "./views/TeamView";
import { AuthProvider } from "./auth/AuthContext";
import { LoginGate } from "./auth/LoginGate";
import { AuthBridge } from "./auth/AuthBridge";

interface Account {
  email: string | null;
  onSignOut: () => void | Promise<void>;
}

// ── Main dashboard ────────────────────────────────────────────────────────────

function Dashboard({ account }: { account?: Account }) {
  const { state, setMode, sendQuery, routeLead } = useRelaySession("live");
  const { call, toast } = useBackend();
  const [nav, setNav] = useState<NavKey>("dashboard");

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

  const onNewAnalysis = () => {
    setNav("dashboard");
    void call("New analysis", () => api.createSession(state.mode), {
      endpoint: "POST /sessions",
      success: "New session started",
    });
  };

  const onSettings = () => toast("Settings — wired to GET /me · pending setup", "info");

  // Sidebar collapse state, remembered across reloads.
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("relay.sidebar") === "collapsed");
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
          onSettings={onSettings}
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
                {nav === "dashboard" && state.mode === "live" && <LiveView state={state} />}
                {nav === "dashboard" && state.mode === "desk" && <DeskView state={state} onQuery={sendQuery} />}
                {nav === "dashboard" && state.mode === "intake" && <IntakeView state={state} onRoute={routeLead} />}
                {nav === "knowledge" && <KnowledgeView />}
                {nav === "transcripts" && <TranscriptsView />}
                {nav === "team" && <TeamView />}
              </motion.div>
            </AnimatePresence>
          </main>
        </div>
      </div>
    </div>
  );
}

// ── Root ──────────────────────────────────────────────────────────────────────
// Auth-gated: sign in, then the session (email + signOut) is bridged into the
// Dashboard via props.

export function App() {
  return (
    <AuthProvider>
      <LoginGate>
        <AuthBridge>{(account) => <Dashboard account={account} />}</AuthBridge>
      </LoginGate>
    </AuthProvider>
  );
}

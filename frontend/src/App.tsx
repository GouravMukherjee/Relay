import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useRelaySession } from "./hooks/useRelaySession";
import { useBackend } from "./backend";
import { api } from "./api/client";
import { easeOut } from "./motion";
import { TopNav } from "./components/TopNav";
import { Sidebar, type NavKey } from "./components/Sidebar";
import { Icon } from "./components/Icon";
import { LiveView } from "./views/LiveView";
import { DeskView } from "./views/DeskView";
import { IntakeView } from "./views/IntakeView";
import { KnowledgeView } from "./views/KnowledgeView";
import { TranscriptsView } from "./views/TranscriptsView";
import { TeamView } from "./views/TeamView";
import { USE_MOCK } from "./config";

// Lazily load auth components only when USE_MOCK=false.
// Vite tree-shakes the entire auth subtree in demo builds.
const AuthProviderLazy = lazy(() =>
  import("./auth/AuthContext").then((m) => ({ default: m.AuthProvider }))
);

const LoginGateLazy = lazy(() =>
  import("./auth/LoginGate").then((m) => ({ default: m.LoginGate }))
);

// ── Main dashboard ────────────────────────────────────────────────────────────

function Dashboard() {
  const { state, setMode, sendQuery, routeLead, playNextBeat, canPlayBeat } =
    useRelaySession("live");
  const { call, toast } = useBackend();
  const [beatsLeft, setBeatsLeft] = useState(true);
  const [nav, setNav] = useState<NavKey>("dashboard");

  // Surface backend connection errors (functional mode) as a toast.
  const lastShownError = useRef<string | null>(null);
  useEffect(() => {
    if (state.lastError && state.lastError !== lastShownError.current) {
      lastShownError.current = state.lastError;
      toast(state.lastError, "error");
    }
  }, [state.lastError, toast]);

  const onBeat = async () => {
    const more = await playNextBeat();
    setBeatsLeft(more);
  };

  const onMode = (m: typeof state.mode) => {
    setMode(m);
    setBeatsLeft(true);
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

  return (
    <div className="app" data-mode={state.mode}>
      <TopNav mode={state.mode} onMode={onMode} onSettings={onSettings} />

      <div className="workspace">
        <Sidebar mode={state.mode} nav={nav} onNav={setNav} onNewAnalysis={onNewAnalysis} status={state.status} />
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

      <AnimatePresence>
        {canPlayBeat && nav === "dashboard" && (
          <motion.button
            className="beat-pill"
            onClick={onBeat}
            disabled={!beatsLeft}
            title="Play the next scripted demo line"
            initial={{ opacity: 0, y: 20, scale: 0.9 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 20, scale: 0.9 }}
            whileHover={{ scale: 1.04, y: -2 }}
            whileTap={{ scale: 0.96 }}
            transition={{ type: "spring", stiffness: 400, damping: 30 }}
          >
            <Icon name="play_arrow" size={16} fill />
            {beatsLeft ? "Next demo beat" : "Demo complete"}
          </motion.button>
        )}
      </AnimatePresence>
    </div>
  );
}

// ── Root ──────────────────────────────────────────────────────────────────────

export function App() {
  if (USE_MOCK) {
    // Mock path: no auth, no Supabase, no login gate. 100% intact.
    return <Dashboard />;
  }

  // Real backend path: wrap in lazily-loaded AuthProvider + LoginGate.
  return (
    <Suspense fallback={<div className="login-loading"><span>Loading…</span></div>}>
      <AuthProviderLazy>
        <LoginGateLazy>
          <Dashboard />
        </LoginGateLazy>
      </AuthProviderLazy>
    </Suspense>
  );
}

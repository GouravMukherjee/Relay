import { useEffect, useRef, useState } from "react";
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

export function App() {
  const { state, setMode, sendQuery, routeLead, playNextBeat, canPlayBeat } = useRelaySession("live");
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

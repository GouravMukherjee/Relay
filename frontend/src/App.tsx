import { useState } from "react";
import { useRelaySession } from "./hooks/useRelaySession";
import { useBackend } from "./backend";
import { api } from "./api/client";
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
        <Sidebar mode={state.mode} nav={nav} onNav={setNav} onNewAnalysis={onNewAnalysis} />
        <main className="main">
          {nav === "dashboard" && state.mode === "live" && <LiveView state={state} />}
          {nav === "dashboard" && state.mode === "desk" && <DeskView state={state} onQuery={sendQuery} />}
          {nav === "dashboard" && state.mode === "intake" && <IntakeView state={state} onRoute={routeLead} />}
          {nav === "knowledge" && <KnowledgeView />}
          {nav === "transcripts" && <TranscriptsView />}
          {nav === "team" && <TeamView />}
        </main>
      </div>

      {canPlayBeat && nav === "dashboard" && (
        <button className="beat-pill" onClick={onBeat} disabled={!beatsLeft} title="Play the next scripted demo line">
          <Icon name="play_arrow" size={16} fill />
          {beatsLeft ? "Next demo beat" : "Demo complete"}
        </button>
      )}
    </div>
  );
}

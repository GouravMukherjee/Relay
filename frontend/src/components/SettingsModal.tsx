import { AnimatePresence, motion } from "framer-motion";
import { useEffect } from "react";
import { easeOut } from "../motion";
import { Icon } from "./Icon";

interface Props {
  open: boolean;
  onClose: () => void;
}

const SECTIONS = [
  {
    title: "Retrieval",
    items: [
      { label: "Primary index", value: "Moss (hybrid search)" },
      { label: "Fallback", value: "pgvector cosine" },
      { label: "Latency target", value: "< 500 ms" },
    ],
  },
  {
    title: "LLM",
    items: [
      { label: "Primary model", value: "TrueFoundry → Claude Sonnet" },
      { label: "Fallback chain", value: "Anthropic direct → Qwen Plus" },
      { label: "Max answer length", value: "600 characters" },
    ],
  },
  {
    title: "Live audio",
    items: [
      { label: "STT provider", value: "LiveKit Inference (AssemblyAI)" },
      { label: "TTS provider", value: "MiniMax Speech-02-Turbo" },
      { label: "Room server", value: "relay-ayfm1fbo.livekit.cloud" },
    ],
  },
  {
    title: "Storage",
    items: [
      { label: "Database", value: "Supabase Postgres + pgvector" },
      { label: "Files", value: "AWS S3 (us-east-2)" },
      { label: "Queue / cache", value: "Redis (arq)" },
    ],
  },
];

export function SettingsModal({ open, onClose }: Props) {
  // Close on Escape.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            className="modal-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
            onClick={onClose}
          />
          {/* Panel */}
          <motion.div
            className="settings-panel"
            initial={{ opacity: 0, x: 40 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 40 }}
            transition={{ duration: 0.22, ease: easeOut }}
          >
            <div className="settings-header">
              <h2 className="settings-title">Settings</h2>
              <button className="settings-close" onClick={onClose} aria-label="Close settings">
                <Icon name="close" size={20} />
              </button>
            </div>

            <div className="settings-body scroll">
              <p className="settings-intro">
                Relay is configured via environment variables. These values are read-only here — edit
                <code>.env</code> to change them.
              </p>

              {SECTIONS.map((s) => (
                <div key={s.title} className="settings-section">
                  <h3 className="settings-section-title label-caps">{s.title}</h3>
                  <div className="settings-rows">
                    {s.items.map((item) => (
                      <div key={item.label} className="settings-row">
                        <span className="settings-row-label">{item.label}</span>
                        <span className="settings-row-value">{item.value}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}

              <div className="settings-section">
                <h3 className="settings-section-title label-caps">Interface</h3>
                <div className="settings-rows">
                  <div className="settings-row">
                    <span className="settings-row-label">Sidebar</span>
                    <span className="settings-row-value">Toggle with the menu icon (top-left)</span>
                  </div>
                  <div className="settings-row">
                    <span className="settings-row-label">Theme</span>
                    <span className="settings-row-value">Functional White (Relay design system)</span>
                  </div>
                  <div className="settings-row">
                    <span className="settings-row-label">Grounding guard</span>
                    <span className="settings-row-value">Enabled — never hallucinates answers</span>
                  </div>
                </div>
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}

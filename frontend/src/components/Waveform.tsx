import { useEffect, useRef, useState } from "react";

// Simulated audio waveform. Center bars are the "active" indigo speaker; the
// flanks stay neutral. When someone is speaking the bars dance; otherwise they
// settle to a quiet idle line. Matches the Stitch dashboard waveform.

const BARS = 16;
const ACTIVE_FROM = 5;
const ACTIVE_TO = 10;

export function Waveform({ active }: { active: boolean }) {
  const [levels, setLevels] = useState<number[]>(() => Array(BARS).fill(0.3));
  const raf = useRef<number>();

  useEffect(() => {
    let t = 0;
    const tick = () => {
      t += 0.09;
      setLevels((prev) =>
        prev.map((_, i) => {
          const isActive = active && i >= ACTIVE_FROM && i <= ACTIVE_TO;
          if (!isActive) return 0.25 + 0.15 * (Math.sin(t * 0.8 + i * 0.6) * 0.5 + 0.5);
          const env = Math.sin(t * 1.8 + i * 0.5) * 0.5 + 0.5;
          return Math.min(1, 0.35 + env * 0.5 + Math.random() * 0.25);
        }),
      );
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current!);
  }, [active]);

  return (
    <div className="waveform" aria-hidden>
      {levels.map((l, i) => {
        const inActive = i >= ACTIVE_FROM && i <= ACTIVE_TO;
        return (
          <span
            key={i}
            className="bar"
            style={{
              height: `${Math.round(l * 100)}%`,
              background: inActive ? "var(--primary-container)" : "var(--surface-variant)",
            }}
          />
        );
      })}
    </div>
  );
}

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { Card } from "../types";
import { Icon } from "./Icon";
import { easeOut } from "../motion";

// Latency counts up to its value on mount — turns a static number into a visible
// "resolved in <500ms" moment, in keeping with the real-time product story.
function useCountUp(target: number) {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    const steps = 16;
    let i = 0;
    const id = setInterval(() => {
      i++;
      setShown(Math.round((target * i) / steps));
      if (i >= steps) clearInterval(id);
    }, 16);
    return () => clearInterval(id);
  }, [target]);
  return shown;
}

interface Props {
  card: Card;
  featured?: boolean;
}

export function RelayCard({ card, featured }: Props) {
  const [openSource, setOpenSource] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const ms = useCountUp(card.latency_ms);
  const primarySource = card.sources[0];

  const copy = () => {
    void navigator.clipboard?.writeText(card.answer);
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  return (
    <motion.div
      layout
      className={`answer-card${featured ? " featured" : ""}`}
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8, transition: { duration: 0.2 } }}
      whileHover={{ y: -4 }}
      transition={{ type: "spring", stiffness: 380, damping: 30 }}
    >
      <div className="answer-top">
        <h3 className="answer-title">{card.title ?? card.trigger_text}</h3>
        <motion.button
          className="copy-btn"
          onClick={copy}
          title="Copy answer"
          whileHover={{ scale: 1.15 }}
          whileTap={{ scale: 0.85 }}
        >
          <Icon name={copied ? "check" : "content_copy"} size={20} />
        </motion.button>
      </div>

      <p className="answer-body">&ldquo;{card.answer}&rdquo;</p>

      <div className="answer-foot">
        <span className="verified">
          <Icon name="check_circle" size={14} fill />
          Verified · {ms}ms
        </span>
        {primarySource && (
          <motion.button
            className="source-chip"
            onClick={() =>
              setOpenSource(openSource === primarySource.document_id ? null : primarySource.document_id)
            }
            title="Expand source"
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.97 }}
          >
            <Icon name="description" size={14} />
            {primarySource.title}
          </motion.button>
        )}
      </div>

      <AnimatePresence initial={false}>
        {primarySource && openSource === primarySource.document_id && (
          <motion.div
            className="source-snippet"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: easeOut }}
            style={{ overflow: "hidden" }}
          >
            {primarySource.snippet}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

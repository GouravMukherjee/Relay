import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ChatWidget } from "./ChatWidget";

const TOPICS = [
  {
    icon: "local_shipping",
    title: "Orders & Shipping",
    body: "Track a delivery, change an address, or check on a delayed shipment.",
  },
  {
    icon: "sync_alt",
    title: "Returns & Refunds",
    body: "Start a return, print a label, and see where your refund stands.",
  },
  {
    icon: "credit_card",
    title: "Billing & Plans",
    body: "Update payment details, download invoices, or talk through pricing.",
  },
];

export default function App() {
  const [chatOpen, setChatOpen] = useState(false);

  return (
    <div className="nw-page">
      {/* Header */}
      <header className="nw-header">
        <div className="nw-header-inner">
          <div className="nw-brand">
            <div className="nw-logo">
              <span className="nw-ms">explore</span>
            </div>
            <div className="nw-brand-text">
              <span className="nw-brand-name">Northwind</span>
              <span className="nw-brand-sub">Help Center</span>
            </div>
          </div>
          <nav className="nw-nav">
            <a href="#">Products</a>
            <a href="#">Docs</a>
            <a href="#">Status</a>
            <a href="#" className="nw-cta" onClick={(e) => { e.preventDefault(); setChatOpen(true); }}>
              Contact us
            </a>
          </nav>
        </div>
      </header>

      {/* Hero */}
      <main className="nw-main">
        <motion.section
          className="nw-hero"
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
        >
          <span className="nw-eyebrow">
            <span className="nw-dot" />
            Support is online
          </span>
          <h1>
            How can we <span className="nw-grad">help you</span> today?
          </h1>
          <p>
            Search our help center or start a chat — our team answers fast with grounded, accurate
            information from Northwind&apos;s own knowledge base.
          </p>
          <div className="nw-search" onClick={() => setChatOpen(true)}>
            <span className="nw-ms">search</span>
            <span>Ask a question or describe your issue…</span>
            <button className="nw-search-btn" onClick={(e) => { e.stopPropagation(); setChatOpen(true); }}>
              Start chat
            </button>
          </div>
        </motion.section>

        <motion.section
          className="nw-topics"
          initial="hidden"
          animate="show"
          variants={{ show: { transition: { staggerChildren: 0.08, delayChildren: 0.15 } } }}
        >
          {TOPICS.map((t) => (
            <motion.button
              key={t.title}
              className="nw-topic"
              onClick={() => setChatOpen(true)}
              variants={{ hidden: { opacity: 0, y: 16 }, show: { opacity: 1, y: 0 } }}
              whileHover={{ y: -4 }}
              transition={{ type: "spring", stiffness: 360, damping: 28 }}
            >
              <div className="nw-topic-ic">
                <span className="nw-ms">{t.icon}</span>
              </div>
              <h3>{t.title}</h3>
              <p>{t.body}</p>
            </motion.button>
          ))}
        </motion.section>
      </main>

      {/* Docked launcher */}
      <AnimatePresence>
        {!chatOpen && (
          <motion.button
            className="nw-launcher"
            onClick={() => setChatOpen(true)}
            aria-label="Open support chat"
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.6 }}
            whileHover={{ scale: 1.08 }}
            whileTap={{ scale: 0.92 }}
            transition={{ type: "spring", stiffness: 420, damping: 24 }}
          >
            <span className="nw-ms">chat</span>
          </motion.button>
        )}
      </AnimatePresence>

      <ChatWidget open={chatOpen} onClose={() => setChatOpen(false)} />
    </div>
  );
}

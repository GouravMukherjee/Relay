import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Icon } from "./Icon";
import { pressable } from "../motion";

// Avatar button + dropdown showing the signed-in email and a sign-out action.
// Presentational only (no auth import) so it stays out of the demo bundle —
// `email` / `onSignOut` are injected from the auth boundary in functional mode.
interface Props {
  email: string | null;
  onSignOut?: () => void;
}

export function AccountMenu({ email, onSignOut }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const initial = (email?.trim()?.[0] ?? "R").toUpperCase();

  // Close on outside click or Escape.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="account" ref={ref}>
      <motion.button
        className="avatar"
        title="Account"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        {...pressable}
      >
        {initial}
      </motion.button>

      <AnimatePresence>
        {open && (
          <motion.div
            className="account-menu"
            role="menu"
            initial={{ opacity: 0, y: -8, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.96 }}
            transition={{ duration: 0.16 }}
          >
            <div className="account-head">
              <div className="account-avatar">{initial}</div>
              <div className="account-id">
                <div className="account-email" title={email ?? undefined}>
                  {email ?? "Demo session"}
                </div>
                <div className="account-sub">{email ? "Signed in" : "Demo mode"}</div>
              </div>
            </div>

            <div className="account-divider" />

            <button
              className="account-action"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onSignOut?.();
              }}
              disabled={!onSignOut}
              title={onSignOut ? undefined : "Sign-out is unavailable in demo mode"}
            >
              <Icon name="logout" size={18} />
              Sign out
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Icon } from "./Icon";
import { pressable } from "../motion";

// Avatar button + dropdown showing the signed-in email and a sign-out action.
// Presentational only (no auth import); `email` / `onSignOut` are injected from
// the auth boundary.
interface Props {
  email: string | null;
  onSignOut?: () => void;
  onAccount?: () => void;
}

export function AccountMenu({ email, onSignOut, onAccount }: Props) {
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
                  {email ?? "Account"}
                </div>
                <div className="account-sub">{email ? "Signed in" : "—"}</div>
              </div>
            </div>

            <div className="account-divider" />

            <button
              className="account-action"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onAccount?.();
              }}
            >
              <Icon name="manage_accounts" size={18} />
              Account settings
            </button>

            <button
              className="account-action account-action-danger"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onSignOut?.();
              }}
              disabled={!onSignOut}
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

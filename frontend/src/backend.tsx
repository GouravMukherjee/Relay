// Backend-call layer. Every interactive control routes its action through
// useBackend().call(...), which hits the gateway and shows pending/success/error
// toasts.

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { ApiError } from "./api/client";

type ToastKind = "pending" | "success" | "error" | "info";
interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
}

interface CallOptions {
  /** HTTP method + path, kept for call-site documentation. */
  endpoint?: string;
  /** Message on success. Defaults to "<label> done". */
  success?: string;
}

interface BackendCtx {
  /** Run a backend action with toast feedback. Returns the result, or undefined. */
  call<T>(label: string, fn: () => Promise<T>, opts?: CallOptions): Promise<T | undefined>;
  toast(text: string, kind?: ToastKind): void;
}

const Ctx = createContext<BackendCtx | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const idRef = useRef(0);

  const push = useCallback((text: string, kind: ToastKind, ttl = 3200) => {
    const id = ++idRef.current;
    setToasts((t) => [...t, { id, kind, text }]);
    if (ttl) setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), ttl);
    return id;
  }, []);

  const remove = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);

  const call = useCallback(
    async <T,>(label: string, fn: () => Promise<T>, opts?: CallOptions): Promise<T | undefined> => {
      const pendingId = push(`${label}…`, "pending", 0);
      try {
        const res = await fn();
        remove(pendingId);
        push(opts?.success ?? `${label} done`, "success");
        return res;
      } catch (e) {
        remove(pendingId);
        const msg = (e as ApiError)?.message || (e as Error)?.message || "request failed";
        push(`${label} failed — ${msg}`, "error", 5000);
        return undefined;
      }
    },
    [push, remove],
  );

  const toast = useCallback((text: string, kind: ToastKind = "info") => push(text, kind), [push]);

  const value = useMemo<BackendCtx>(() => ({ call, toast }), [call, toast]);

  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="toast-stack">
        <AnimatePresence initial={false}>
          {toasts.map((t) => (
            <motion.div
              key={t.id}
              layout
              className={`toast ${t.kind}`}
              onClick={() => remove(t.id)}
              initial={{ opacity: 0, x: -24, scale: 0.96 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: -24, scale: 0.96 }}
              transition={{ type: "spring", stiffness: 400, damping: 32 }}
            >
              <span className="toast-dot" />
              {t.text}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </Ctx.Provider>
  );
}

export function useBackend(): BackendCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useBackend must be used within ToastProvider");
  return ctx;
}

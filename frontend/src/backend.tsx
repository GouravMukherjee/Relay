// Backend-call layer. Every interactive control routes its action through
// useBackend().call(...), which targets the real gateway when one is configured
// (VITE_USE_MOCK=false) and degrades to an informative toast in demo mode — so
// the whole UI is "wired for backend" today and live the moment it exists.

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { USE_MOCK } from "./config";
import type { ApiError } from "./api/client";

type ToastKind = "pending" | "success" | "error" | "info";
interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
}

interface CallOptions {
  /** HTTP method + path shown in demo toasts, e.g. "POST /documents". */
  endpoint?: string;
  /** Message on success (real backend). Defaults to "<label> done". */
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
      // Demo mode: no gateway to talk to — show what *would* be sent.
      if (USE_MOCK) {
        push(`${label} → ${opts?.endpoint ?? "backend"} · pending setup`, "info");
        return undefined;
      }
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
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`} onClick={() => remove(t.id)}>
            <span className="toast-dot" />
            {t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useBackend(): BackendCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useBackend must be used within ToastProvider");
  return ctx;
}

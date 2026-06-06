// Fetch a backend resource for the dashboard section tabs. Against a real gateway
// it fetches on mount; in demo mode it returns bundled demo data (flagged `demo`)
// so the tab is populated and obviously "wired, backend pending".

import { useCallback, useEffect, useState } from "react";
import { USE_MOCK } from "../config";
import type { ApiError } from "../api/client";

export interface Resource<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  demo: boolean;
  reload: () => void;
}

export function useResource<T>(fetcher: () => Promise<T>, demoData: T): Resource<T> {
  const [data, setData] = useState<T | null>(USE_MOCK ? demoData : null);
  const [loading, setLoading] = useState(!USE_MOCK);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (USE_MOCK) return;
    let alive = true;
    setLoading(true);
    setError(null);
    fetcher()
      .then((d) => alive && setData(d))
      .catch((e: ApiError) => alive && setError(e?.message ?? "request failed"))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick]);

  return { data, loading, error, demo: USE_MOCK, reload };
}

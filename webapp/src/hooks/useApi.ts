import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  status: number | null; // HTTP-ish status (404 => "not run yet")
  // True when `data` was served from client.ts's localStorage offline-cache
  // fallback (the network was unreachable) rather than a live response.
  stale: boolean;
  cachedAt: string | null; // ISO timestamp the stale `data` was cached at
  reload: () => void;
}

/**
 * Generic async loader. `fn` is re-invoked whenever any value in `deps` changes.
 * Distinguishes a 404 (honest "not produced yet") from a hard error via `status`.
 */
export function useApi<T>(
  fn: () => Promise<T>,
  deps: unknown[] = []
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [stale, setStale] = useState(false);
  const [cachedAt, setCachedAt] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const alive = useRef(true);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    alive.current = true;
    setLoading(true);
    setError(null);
    setStatus(null);
    fn()
      .then((d) => {
        if (!alive.current) return;
        setData(d);
        setStale(false);
        setCachedAt(null);
      })
      .catch((e: unknown) => {
        if (!alive.current) return;
        if (e instanceof ApiError && e.cachedData !== undefined) {
          // Offline fallback: render the cached response as real data (not an
          // error screen) and flag it `stale` so a screen can note it's cached.
          setData(e.cachedData as T);
          setStale(true);
          setCachedAt(e.cachedAt ?? null);
          setError(null);
          setStatus(null);
          return;
        }
        setData(null);
        setStale(false);
        setCachedAt(null);
        if (e instanceof ApiError) {
          setError(e.message);
          setStatus(e.status);
        } else {
          setError(e instanceof Error ? e.message : "Unknown error");
          setStatus(null);
        }
      })
      .finally(() => {
        if (alive.current) setLoading(false);
      });
    return () => {
      alive.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  return { data, loading, error, status, stale, cachedAt, reload };
}

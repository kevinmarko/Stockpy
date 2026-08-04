import { useCallback, useEffect, useState } from "react";
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

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    // A per-invocation flag, NOT a shared ref: this effect re-runs on every
    // `reload()` (tick changes), and a shared `useRef` reset to `true` at
    // the top of EACH invocation would let an OLDER, still-in-flight fetch's
    // `.then` pass its "still alive" check just because a NEWER invocation
    // already flipped the shared ref back to true -- letting a stale
    // response overwrite state a newer, already-resolved reload() correctly
    // set. Capturing `cancelled` fresh in this closure means only THIS
    // invocation's own cleanup can ever flip it, so an old response from a
    // superseded reload() is reliably ignored regardless of resolution
    // order between overlapping calls (e.g. two reload()s fired back to
    // back before the first's response lands).
    let cancelled = false;
    setLoading(true);
    setError(null);
    setStatus(null);
    fn()
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setStale(false);
        setCachedAt(null);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
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
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  return { data, loading, error, status, stale, cachedAt, reload };
}

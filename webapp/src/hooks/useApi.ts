import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  status: number | null; // HTTP-ish status (404 => "not run yet")
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
      })
      .catch((e: unknown) => {
        if (!alive.current) return;
        setData(null);
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

  return { data, loading, error, status, reload };
}

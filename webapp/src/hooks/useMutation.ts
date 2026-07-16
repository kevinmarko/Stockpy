import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Codifies the hand-rolled write-path shape used by FollowModal.tsx (the
 * only mutation in the app before this): local pending/result/error state,
 * try/catch/finally, `instanceof Error` narrowing. `useApi` is deliberately
 * read-only (it re-runs `fn` on a dependency change, not on demand); this is
 * its write-side counterpart, not a replacement — a screen with a five-way
 * write surface (run / set-interval / pause / resume / re-plan) shouldn't
 * hand-roll that ~12-line block five times.
 */
export function useMutation<TArgs extends unknown[], TResult>(
  fn: (...args: TArgs) => Promise<TResult>
) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TResult | null>(null);
  const alive = useRef(true);

  // Mirrors useApi.ts's alive-ref pattern -- a mutation whose response
  // arrives after the component unmounted (e.g. the user navigated away
  // mid-request) must not call setState on an unmounted component.
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const run = useCallback(
    async (...args: TArgs): Promise<TResult | undefined> => {
      setPending(true);
      setError(null);
      try {
        const r = await fn(...args);
        if (alive.current) setResult(r);
        return r;
      } catch (e) {
        if (alive.current) {
          setError(e instanceof Error ? e.message : "Request failed");
        }
        return undefined;
      } finally {
        if (alive.current) setPending(false);
      }
    },
    [fn]
  );

  const reset = useCallback(() => {
    setPending(false);
    setError(null);
    setResult(null);
  }, []);

  return { run, pending, error, result, reset };
}

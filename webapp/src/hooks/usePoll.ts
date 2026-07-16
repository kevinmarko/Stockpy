import { useEffect, useRef } from "react";

/**
 * Calls `reload` every `ms` milliseconds, but ONLY while `enabled` is true.
 * Meant to pair with `useApi`'s `reload` — e.g. poll a run-status screen every
 * few seconds while a run is in flight, and stop the instant it terminates,
 * rather than polling a phone's radio for a status that changes once every
 * five minutes. `useApi` itself has no polling; this is the add-on for the
 * one screen (Settings' pipeline status) that needs it.
 *
 * `reload` is captured in a ref so a non-memoized callback identity doesn't
 * restart the interval on every render — only `enabled`/`ms` do that.
 */
export function usePoll(reload: () => void, ms: number, enabled: boolean): void {
  const reloadRef = useRef(reload);
  reloadRef.current = reload;

  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => reloadRef.current(), ms);
    return () => clearInterval(id);
  }, [enabled, ms]);
}

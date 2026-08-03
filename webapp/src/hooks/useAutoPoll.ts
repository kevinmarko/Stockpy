import { useCallback, useEffect, useRef, useState } from "react";
import { useAutoRefresh, type AutoRefreshCategory } from "../components/AutoRefreshContext";
import { usePoll } from "./usePoll";

// Re-exported so existing call sites can keep importing the category type
// from here -- the union itself is defined once, in AutoRefreshContext.tsx,
// not duplicated across the two files.
export type { AutoRefreshCategory };

export interface UseAutoPollOptions {
  enabled?: boolean;
  hasError?: boolean;
  customIntervalMs?: number;
}

const MAX_BACKOFF_MS = 300_000; // 5 minutes max backoff cap

/**
 * Custom hook combining `useAutoRefresh` global context state, page visibility (tab switching),
 * market session checks (pause on weekends/closed market), and error backoff logic.
 */
export function useAutoPoll(
  reload: () => void,
  category?: AutoRefreshCategory,
  options?: UseAutoPollOptions
): void {
  const {
    autoRefreshEnabled,
    pauseWhenMarketClosed,
    isMarketOpen,
    isTabVisible,
    portfolioRefreshEnabled,
    dashboardRefreshEnabled,
    signalsRefreshEnabled,
    observabilityRefreshEnabled,
    optionsRefreshEnabled,
    robinhoodRefreshEnabled,
    resolveIntervalMs,
  } = useAutoRefresh();

  const [consecutiveErrors, setConsecutiveErrors] = useState<number>(0);
  const reloadRef = useRef(reload);
  reloadRef.current = reload;

  // Exponential backoff on sustained errors. Two separate mechanisms, on
  // purpose: the effect below only fires on a hasError TRANSITION (an effect
  // keyed on a primitive boolean dependency never reruns while that boolean
  // stays the same value across renders) -- it earns the *first* backoff step
  // immediately, and resets to 0 the instant an error clears, without waiting
  // for the next scheduled tick. Escalating FURTHER while an error persists
  // across many polls has to happen in the tick handler itself (below),
  // since that's the only place that actually observes "another poll just
  // happened and it's still erroring" -- the effect has no way to see that.
  const hasError = options?.hasError ?? false;
  const hasErrorRef = useRef(hasError);
  hasErrorRef.current = hasError;

  useEffect(() => {
    if (hasError) {
      setConsecutiveErrors((c) => (c === 0 ? 1 : c));
    } else {
      setConsecutiveErrors(0);
    }
  }, [hasError]);

  const handleTick = useCallback(() => {
    if (hasErrorRef.current) {
      // Cap the raw step count well below where it would matter -- this only
      // bounds Math.pow's growth, the real ceiling is MAX_BACKOFF_MS below.
      setConsecutiveErrors((c) => Math.min(c + 1, 20));
    }
    reloadRef.current();
  }, []);

  // Determine category toggle state
  let categoryEnabled = true;
  if (category === "portfolio") categoryEnabled = portfolioRefreshEnabled;
  else if (category === "dashboard") categoryEnabled = dashboardRefreshEnabled;
  else if (category === "signals") categoryEnabled = signalsRefreshEnabled;
  else if (category === "observability") categoryEnabled = observabilityRefreshEnabled;
  else if (category === "options") categoryEnabled = optionsRefreshEnabled;
  else if (category === "robinhood") categoryEnabled = robinhoodRefreshEnabled;

  const baseEnabled = options?.enabled ?? true;
  const marketSessionAllowed = !pauseWhenMarketClosed || isMarketOpen;

  // Every enabled predicate EXCEPT visibility itself -- gating the catch-up
  // reload below on the full effectiveEnabled (which includes isTabVisible)
  // is a trap: at the instant the visibility listener fires, isTabVisible
  // hasn't flipped to true in THIS render yet, so that would make the
  // catch-up reload never fire at all.
  const autoRefreshActive =
    autoRefreshEnabled && categoryEnabled && baseEnabled && marketSessionAllowed;
  const autoRefreshActiveRef = useRef(autoRefreshActive);
  autoRefreshActiveRef.current = autoRefreshActive;

  // Catch-up reload on tab refocus. isTabVisible is a single app-wide
  // subscription (AutoRefreshContext), not a per-instance listener -- fires
  // once per hidden->visible transition, only if the poller would otherwise
  // be running (ref-guarded against a toggle flip while already visible).
  const prevVisibleRef = useRef(isTabVisible);
  useEffect(() => {
    if (isTabVisible && !prevVisibleRef.current && autoRefreshActiveRef.current) {
      reloadRef.current();
    }
    prevVisibleRef.current = isTabVisible;
  }, [isTabVisible]);

  const effectiveEnabled = autoRefreshActive && isTabVisible;

  // Calculate interval with optional exponential backoff on errors. A
  // per-category override (e.g. the Robinhood 15 min floor) resolves through
  // resolveIntervalMs; customIntervalMs (a caller-supplied cadence FLOOR for
  // a heavy composite poll) always wins over both.
  const baseInterval = options?.customIntervalMs ?? resolveIntervalMs(category);
  let effectiveInterval = baseInterval;

  if (consecutiveErrors > 0) {
    const backoffMultiplier = Math.pow(1.5, Math.min(consecutiveErrors, 8));
    // Math.max keeps this a genuine backoff even when baseInterval already
    // exceeds MAX_BACKOFF_MS (e.g. the Robinhood category's 1h default) --
    // without it, Math.min(MAX_BACKOFF_MS, ...) alone can produce an
    // interval SHORTER than the base, which is exactly backwards for the
    // category where repeated failed logins are the most costly to retry.
    effectiveInterval = Math.max(
      baseInterval,
      Math.min(MAX_BACKOFF_MS, Math.round(baseInterval * backoffMultiplier))
    );
  }

  usePoll(handleTick, effectiveInterval, effectiveEnabled);
}

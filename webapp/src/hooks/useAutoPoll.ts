import { useEffect, useState, useRef } from "react";
import { useAutoRefresh } from "../components/AutoRefreshContext";
import { usePoll } from "./usePoll";

export type AutoRefreshCategory =
  | "portfolio"
  | "dashboard"
  | "signals"
  | "observability"
  | "options";

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
    autoRefreshIntervalMs,
    portfolioRefreshEnabled,
    dashboardRefreshEnabled,
    signalsRefreshEnabled,
    observabilityRefreshEnabled,
    optionsRefreshEnabled,
    isMarketOpen,
  } = useAutoRefresh();

  const [isVisible, setIsVisible] = useState<boolean>(() => {
    return typeof document !== "undefined" ? document.visibilityState === "visible" : true;
  });

  const [consecutiveErrors, setConsecutiveErrors] = useState<number>(0);
  const reloadRef = useRef(reload);
  reloadRef.current = reload;

  // Track error changes for exponential backoff
  const hasError = options?.hasError ?? false;
  const prevErrorRef = useRef(hasError);

  useEffect(() => {
    if (hasError) {
      if (!prevErrorRef.current) {
        setConsecutiveErrors(1);
      } else {
        setConsecutiveErrors((c) => c + 1);
      }
    } else {
      setConsecutiveErrors(0);
    }
    prevErrorRef.current = hasError;
  }, [hasError]);

  // Page Visibility API listener
  useEffect(() => {
    if (typeof document === "undefined") return;

    const handleVisibilityChange = () => {
      const visible = document.visibilityState === "visible";
      setIsVisible(visible);
      // Trigger catch-up reload upon returning to the tab if auto-refresh is active
      if (visible) {
        reloadRef.current();
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);

  // Determine category toggle state
  let categoryEnabled = true;
  if (category === "portfolio") categoryEnabled = portfolioRefreshEnabled;
  else if (category === "dashboard") categoryEnabled = dashboardRefreshEnabled;
  else if (category === "signals") categoryEnabled = signalsRefreshEnabled;
  else if (category === "observability") categoryEnabled = observabilityRefreshEnabled;
  else if (category === "options") categoryEnabled = optionsRefreshEnabled;

  const baseEnabled = options?.enabled ?? true;
  const marketSessionAllowed = !pauseWhenMarketClosed || isMarketOpen;

  const effectiveEnabled =
    autoRefreshEnabled && categoryEnabled && baseEnabled && isVisible && marketSessionAllowed;

  // Calculate interval with optional exponential backoff on errors
  const baseInterval = options?.customIntervalMs ?? autoRefreshIntervalMs;
  let effectiveInterval = baseInterval;

  if (consecutiveErrors > 0) {
    const backoffMultiplier = Math.pow(1.5, Math.min(consecutiveErrors, 8));
    effectiveInterval = Math.min(MAX_BACKOFF_MS, Math.round(baseInterval * backoffMultiplier));
  }

  usePoll(reload, effectiveInterval, effectiveEnabled);
}

import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { computeMarketSession } from "../marketSession";

export type AutoRefreshCategory =
  | "portfolio"
  | "dashboard"
  | "signals"
  | "observability"
  | "options"
  // Robinhood-backed reads can trigger a real broker login (see
  // ROBINHOOD_AUTO_REFRESH_ENABLED in CLAUDE.md) -- a structurally different
  // cost from every other category (a local DB read), which is why this one
  // alone defaults OFF and carries its own interval floor below.
  | "robinhood";

export interface AutoRefreshContextValue {
  autoRefreshEnabled: boolean;
  pauseWhenMarketClosed: boolean;
  autoRefreshIntervalMs: number;
  portfolioRefreshEnabled: boolean;
  dashboardRefreshEnabled: boolean;
  signalsRefreshEnabled: boolean;
  observabilityRefreshEnabled: boolean;
  optionsRefreshEnabled: boolean;
  robinhoodRefreshEnabled: boolean;
  /** Independent of the master toggle -- governs ONLY the kill-switch/
   *  automation poll (TopStatusBar). Defaults ON, opposite convention from
   *  the master switch, since a stale kill-switch reading is a safety issue,
   *  not a battery optimization. */
  safetyTelemetryEnabled: boolean;
  /** Single app-wide Page Visibility subscription -- dedupes what used to be
   *  one `visibilitychange` listener per `useAutoPoll` instance. */
  isTabVisible: boolean;
  isMarketOpen: boolean;
  /** Sparse per-category interval overrides (ms). Absent entries fall back to
   *  `autoRefreshIntervalMs` via `resolveIntervalMs`. */
  categoryIntervalMs: Partial<Record<AutoRefreshCategory, number>>;
  setAutoRefreshEnabled: (enabled: boolean) => void;
  setPauseWhenMarketClosed: (pause: boolean) => void;
  setAutoRefreshIntervalMs: (ms: number) => void;
  setCategoryRefreshEnabled: (category: AutoRefreshCategory, enabled: boolean) => void;
  setCategoryIntervalMs: (category: AutoRefreshCategory, ms: number) => void;
  setSafetyTelemetryEnabled: (enabled: boolean) => void;
  toggleAutoRefresh: () => void;
  /** Resolves the effective poll interval for a category: no category, or a
   *  category with no override, falls back to the global interval; a
   *  category with an override gets it re-clamped through
   *  `clampCategoryIntervalMs` (localStorage is hand-editable from devtools,
   *  so the floor is re-applied on read, not just on write). */
  resolveIntervalMs: (category?: AutoRefreshCategory) => number;
}

const STORAGE_KEYS = {
  MASTER: "stockpy.auto_refresh.enabled",
  PAUSE_CLOSED: "stockpy.auto_refresh.pause_when_closed",
  INTERVAL: "stockpy.auto_refresh.interval_ms",
  PORTFOLIO: "stockpy.auto_refresh.portfolio_enabled",
  DASHBOARD: "stockpy.auto_refresh.dashboard_enabled",
  SIGNALS: "stockpy.auto_refresh.signals_enabled",
  OBSERVABILITY: "stockpy.auto_refresh.observability_enabled",
  OPTIONS: "stockpy.auto_refresh.options_enabled",
  ROBINHOOD: "stockpy.auto_refresh.robinhood_enabled",
  SAFETY_TELEMETRY: "stockpy.auto_refresh.safety_telemetry_enabled",
};

const DEFAULT_INTERVAL_MS = 30_000;
const MIN_INTERVAL_MS = 5_000;
const MAX_INTERVAL_MS = 86_400_000;

export function clampIntervalMs(ms: number): number {
  if (isNaN(ms) || !isFinite(ms)) return DEFAULT_INTERVAL_MS;
  return Math.max(MIN_INTERVAL_MS, Math.min(MAX_INTERVAL_MS, ms));
}

interface CategoryIntervalRule {
  min: number;
  default: number;
}

/** Per-category interval floors/defaults. Only categories with a real reason
 *  to diverge from the global interval need an entry here -- everything else
 *  falls through `clampIntervalMs`'s ordinary [5s, 24h] bounds. */
export const CATEGORY_INTERVAL_RULES: Partial<Record<AutoRefreshCategory, CategoryIntervalRule>> = {
  robinhood: { min: 900_000 /* 15 min floor */, default: 3_600_000 /* 1h */ },
};

const CATEGORY_INTERVAL_STORAGE_KEYS: Partial<Record<AutoRefreshCategory, string>> = {
  robinhood: "stockpy.auto_refresh.robinhood_interval_ms",
};

/** Re-clamps a per-category interval on READ as well as on write -- a floor
 *  that only exists in the setter isn't really a floor once localStorage is
 *  hand-editable from devtools. */
export function clampCategoryIntervalMs(category: AutoRefreshCategory, ms: number): number {
  const rule = CATEGORY_INTERVAL_RULES[category];
  if (!rule) return clampIntervalMs(ms);
  if (isNaN(ms) || !isFinite(ms)) return rule.default;
  return Math.min(MAX_INTERVAL_MS, Math.max(rule.min, ms));
}

// ---------------------------------------------------------------------------
// localStorage access, guarded. `localStorage` throws in Safari private
// browsing and when storage is disabled by policy; since these reads run in
// the provider's lazy initializers and the provider wraps the entire app,
// an unguarded throw here blanks the whole app. House pattern: onboarding.ts.
// ---------------------------------------------------------------------------

function readFlag(key: string, fallback: boolean): boolean {
  try {
    const val = localStorage.getItem(key);
    return val === null ? fallback : val === "1";
  } catch {
    return fallback;
  }
}

function writeFlag(key: string, value: boolean): void {
  try {
    localStorage.setItem(key, value ? "1" : "0");
  } catch {
    /* ignore -- localStorage unavailable (Safari private browsing, policy) */
  }
}

function readIntervalMs(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.INTERVAL);
    return clampIntervalMs(raw === null ? NaN : parseInt(raw, 10));
  } catch {
    return DEFAULT_INTERVAL_MS;
  }
}

/** Returns the clamped value actually stored, so callers can sync state to it. */
function writeIntervalMs(ms: number): number {
  const clamped = clampIntervalMs(ms);
  try {
    localStorage.setItem(STORAGE_KEYS.INTERVAL, String(clamped));
  } catch {
    /* ignore */
  }
  return clamped;
}

function readCategoryIntervalOverrides(): Partial<Record<AutoRefreshCategory, number>> {
  const result: Partial<Record<AutoRefreshCategory, number>> = {};
  for (const category of Object.keys(CATEGORY_INTERVAL_STORAGE_KEYS) as AutoRefreshCategory[]) {
    const key = CATEGORY_INTERVAL_STORAGE_KEYS[category];
    if (!key) continue;
    try {
      const raw = localStorage.getItem(key);
      if (raw !== null) {
        result[category] = clampCategoryIntervalMs(category, parseInt(raw, 10));
      }
    } catch {
      // Leave unset -- resolveIntervalMs falls back to the global interval.
    }
  }
  return result;
}

/** Returns the clamped value actually stored, so callers can sync state to it. */
function writeCategoryIntervalMs(category: AutoRefreshCategory, ms: number): number {
  const clamped = clampCategoryIntervalMs(category, ms);
  const key = CATEGORY_INTERVAL_STORAGE_KEYS[category];
  if (key) {
    try {
      localStorage.setItem(key, String(clamped));
    } catch {
      /* ignore */
    }
  }
  return clamped;
}

function isDocumentVisible(): boolean {
  return typeof document !== "undefined" ? document.visibilityState === "visible" : true;
}

const AutoRefreshContext = createContext<AutoRefreshContextValue | undefined>(undefined);

export const AutoRefreshProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [autoRefreshEnabled, setAutoRefreshEnabledState] = useState<boolean>(() =>
    readFlag(STORAGE_KEYS.MASTER, false)
  );

  const [pauseWhenMarketClosed, setPauseWhenMarketClosedState] = useState<boolean>(() =>
    readFlag(STORAGE_KEYS.PAUSE_CLOSED, true)
  );

  const [autoRefreshIntervalMs, setAutoRefreshIntervalMsState] = useState<number>(readIntervalMs);

  const [portfolioRefreshEnabled, setPortfolioRefreshEnabledState] = useState<boolean>(() =>
    readFlag(STORAGE_KEYS.PORTFOLIO, true)
  );

  const [dashboardRefreshEnabled, setDashboardRefreshEnabledState] = useState<boolean>(() =>
    readFlag(STORAGE_KEYS.DASHBOARD, true)
  );

  const [signalsRefreshEnabled, setSignalsRefreshEnabledState] = useState<boolean>(() =>
    readFlag(STORAGE_KEYS.SIGNALS, true)
  );

  const [observabilityRefreshEnabled, setObservabilityRefreshEnabledState] = useState<boolean>(() =>
    readFlag(STORAGE_KEYS.OBSERVABILITY, true)
  );

  const [optionsRefreshEnabled, setOptionsRefreshEnabledState] = useState<boolean>(() =>
    readFlag(STORAGE_KEYS.OPTIONS, true)
  );

  // Deliberately defaults OFF, unlike every other category above -- every
  // other category costs a local DB read, this one costs a real broker
  // login (see the AutoRefreshCategory doc comment above).
  const [robinhoodRefreshEnabled, setRobinhoodRefreshEnabledState] = useState<boolean>(() =>
    readFlag(STORAGE_KEYS.ROBINHOOD, false)
  );

  // Deliberately defaults ON, opposite convention from the master toggle --
  // a stale kill-switch reading is a safety issue, not a battery optimization.
  const [safetyTelemetryEnabled, setSafetyTelemetryEnabledState] = useState<boolean>(() =>
    readFlag(STORAGE_KEYS.SAFETY_TELEMETRY, true)
  );

  const [categoryIntervalMs, setCategoryIntervalMsState] = useState<
    Partial<Record<AutoRefreshCategory, number>>
  >(readCategoryIntervalOverrides);

  const [isMarketOpen, setIsMarketOpen] = useState<boolean>(
    () => computeMarketSession(new Date()) !== "CLOSED"
  );

  const [isTabVisible, setIsTabVisible] = useState<boolean>(isDocumentVisible);

  // Market session re-evaluated on a 60s timer, mirroring TopStatusBar's own
  // pattern -- otherwise "pause when market closed" never resumes on its own,
  // since nothing else re-renders the provider when the clock crosses the
  // open/close boundary. Storing the boolean (not the raw session string)
  // means React bails out of re-rendering on 59 of every 60 ticks.
  useEffect(() => {
    const interval = setInterval(() => {
      setIsMarketOpen(computeMarketSession(new Date()) !== "CLOSED");
    }, 60_000);
    return () => clearInterval(interval);
  }, []);

  // Single app-wide Page Visibility subscription -- replaces what used to be
  // one `visibilitychange` listener per `useAutoPoll` instance.
  useEffect(() => {
    if (typeof document === "undefined") return;
    const handleVisibilityChange = () => setIsTabVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, []);

  // Every setter below is wrapped in useCallback with a stable (empty, save
  // for toggleAutoRefresh) dependency array. Settings.tsx's custom-interval
  // input debounces 500ms keyed on setAutoRefreshIntervalMs's identity; an
  // unstable setter would tear down and reschedule that timer on every
  // render. Do NOT add useMemo around the context value itself -- no
  // provider in this codebase does (DensityContext, ToastContext).

  const setAutoRefreshEnabled = useCallback((enabled: boolean) => {
    setAutoRefreshEnabledState(enabled);
    writeFlag(STORAGE_KEYS.MASTER, enabled);
  }, []);

  const setPauseWhenMarketClosed = useCallback((pause: boolean) => {
    setPauseWhenMarketClosedState(pause);
    writeFlag(STORAGE_KEYS.PAUSE_CLOSED, pause);
  }, []);

  const setAutoRefreshIntervalMs = useCallback((ms: number) => {
    const clamped = writeIntervalMs(ms);
    setAutoRefreshIntervalMsState(clamped);
  }, []);

  const setSafetyTelemetryEnabled = useCallback((enabled: boolean) => {
    setSafetyTelemetryEnabledState(enabled);
    writeFlag(STORAGE_KEYS.SAFETY_TELEMETRY, enabled);
  }, []);

  const setCategoryRefreshEnabled = useCallback(
    (category: AutoRefreshCategory, enabled: boolean) => {
      if (category === "portfolio") {
        setPortfolioRefreshEnabledState(enabled);
        writeFlag(STORAGE_KEYS.PORTFOLIO, enabled);
      } else if (category === "dashboard") {
        setDashboardRefreshEnabledState(enabled);
        writeFlag(STORAGE_KEYS.DASHBOARD, enabled);
      } else if (category === "signals") {
        setSignalsRefreshEnabledState(enabled);
        writeFlag(STORAGE_KEYS.SIGNALS, enabled);
      } else if (category === "observability") {
        setObservabilityRefreshEnabledState(enabled);
        writeFlag(STORAGE_KEYS.OBSERVABILITY, enabled);
      } else if (category === "options") {
        setOptionsRefreshEnabledState(enabled);
        writeFlag(STORAGE_KEYS.OPTIONS, enabled);
      } else if (category === "robinhood") {
        setRobinhoodRefreshEnabledState(enabled);
        writeFlag(STORAGE_KEYS.ROBINHOOD, enabled);
      }
    },
    []
  );

  const setCategoryIntervalMs = useCallback((category: AutoRefreshCategory, ms: number) => {
    const clamped = writeCategoryIntervalMs(category, ms);
    setCategoryIntervalMsState((prev) => ({ ...prev, [category]: clamped }));
  }, []);

  // Deliberately keeps [autoRefreshEnabled, setAutoRefreshEnabled] as deps
  // rather than reading current state via an updater function -- writing to
  // localStorage inside a state updater would double-fire under
  // React.StrictMode, which main.tsx already uses.
  const toggleAutoRefresh = useCallback(() => {
    setAutoRefreshEnabled(!autoRefreshEnabled);
  }, [autoRefreshEnabled, setAutoRefreshEnabled]);

  const resolveIntervalMs = useCallback(
    (category?: AutoRefreshCategory): number => {
      if (!category) return autoRefreshIntervalMs;
      const override = categoryIntervalMs[category];
      if (override == null) return autoRefreshIntervalMs;
      return clampCategoryIntervalMs(category, override);
    },
    [autoRefreshIntervalMs, categoryIntervalMs]
  );

  return (
    <AutoRefreshContext.Provider
      value={{
        autoRefreshEnabled,
        pauseWhenMarketClosed,
        autoRefreshIntervalMs,
        portfolioRefreshEnabled,
        dashboardRefreshEnabled,
        signalsRefreshEnabled,
        observabilityRefreshEnabled,
        optionsRefreshEnabled,
        robinhoodRefreshEnabled,
        safetyTelemetryEnabled,
        isTabVisible,
        isMarketOpen,
        categoryIntervalMs,
        setAutoRefreshEnabled,
        setPauseWhenMarketClosed,
        setAutoRefreshIntervalMs,
        setCategoryRefreshEnabled,
        setCategoryIntervalMs,
        setSafetyTelemetryEnabled,
        toggleAutoRefresh,
        resolveIntervalMs,
      }}
    >
      {children}
    </AutoRefreshContext.Provider>
  );
};

// Module-scope stable no-ops for the out-of-provider fallback -- rebuilding
// these fresh on every call to useAutoRefresh() outside a provider would
// thrash any effect keyed on a consumer using the fallback.
const fallbackNoopSetBoolean = (_enabled: boolean): void => {};
const fallbackNoopSetNumber = (_ms: number): void => {};
const fallbackNoopSetCategory = (_category: AutoRefreshCategory, _enabled: boolean): void => {};
const fallbackNoopSetCategoryInterval = (_category: AutoRefreshCategory, _ms: number): void => {};
const fallbackNoopToggle = (): void => {};
const fallbackResolveIntervalMs = (_category?: AutoRefreshCategory): number => DEFAULT_INTERVAL_MS;

export const useAutoRefresh = (): AutoRefreshContextValue => {
  const ctx = useContext(AutoRefreshContext);
  if (!ctx) {
    // Defensive fallback when used outside AutoRefreshProvider (e.g. isolated
    // unit tests). No lifecycle to hang a 60s timer or a visibilitychange
    // listener on here, so isMarketOpen/isTabVisible are computed inline.
    return {
      autoRefreshEnabled: false,
      pauseWhenMarketClosed: true,
      autoRefreshIntervalMs: DEFAULT_INTERVAL_MS,
      portfolioRefreshEnabled: true,
      dashboardRefreshEnabled: true,
      signalsRefreshEnabled: true,
      observabilityRefreshEnabled: true,
      optionsRefreshEnabled: true,
      robinhoodRefreshEnabled: false,
      safetyTelemetryEnabled: true,
      isTabVisible: true,
      isMarketOpen: computeMarketSession(new Date()) !== "CLOSED",
      categoryIntervalMs: {},
      setAutoRefreshEnabled: fallbackNoopSetBoolean,
      setPauseWhenMarketClosed: fallbackNoopSetBoolean,
      setAutoRefreshIntervalMs: fallbackNoopSetNumber,
      setCategoryRefreshEnabled: fallbackNoopSetCategory,
      setCategoryIntervalMs: fallbackNoopSetCategoryInterval,
      setSafetyTelemetryEnabled: fallbackNoopSetBoolean,
      toggleAutoRefresh: fallbackNoopToggle,
      resolveIntervalMs: fallbackResolveIntervalMs,
    };
  }
  return ctx;
};

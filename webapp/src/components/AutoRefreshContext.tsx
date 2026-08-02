import React, { createContext, useContext, useState } from "react";
import { computeMarketSession } from "./TopStatusBar";

export interface AutoRefreshContextValue {
  autoRefreshEnabled: boolean;
  pauseWhenMarketClosed: boolean;
  autoRefreshIntervalMs: number;
  portfolioRefreshEnabled: boolean;
  dashboardRefreshEnabled: boolean;
  signalsRefreshEnabled: boolean;
  observabilityRefreshEnabled: boolean;
  optionsRefreshEnabled: boolean;
  setAutoRefreshEnabled: (enabled: boolean) => void;
  setPauseWhenMarketClosed: (pause: boolean) => void;
  setAutoRefreshIntervalMs: (ms: number) => void;
  setCategoryRefreshEnabled: (
    category: "portfolio" | "dashboard" | "signals" | "observability" | "options",
    enabled: boolean
  ) => void;
  toggleAutoRefresh: () => void;
  isMarketOpen: boolean;
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
};

const DEFAULT_INTERVAL_MS = 30_000;
const MIN_INTERVAL_MS = 5_000;
const MAX_INTERVAL_MS = 86_400_000;

export function clampIntervalMs(ms: number): number {
  if (isNaN(ms) || !isFinite(ms)) return DEFAULT_INTERVAL_MS;
  return Math.max(MIN_INTERVAL_MS, Math.min(MAX_INTERVAL_MS, ms));
}

const AutoRefreshContext = createContext<AutoRefreshContextValue | undefined>(undefined);

export const AutoRefreshProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [autoRefreshEnabled, setAutoRefreshEnabledState] = useState<boolean>(() => {
    return localStorage.getItem(STORAGE_KEYS.MASTER) === "1";
  });

  const [pauseWhenMarketClosed, setPauseWhenMarketClosedState] = useState<boolean>(() => {
    const val = localStorage.getItem(STORAGE_KEYS.PAUSE_CLOSED);
    return val === null ? true : val === "1";
  });

  const [autoRefreshIntervalMs, setAutoRefreshIntervalMsState] = useState<number>(() => {
    const raw = localStorage.getItem(STORAGE_KEYS.INTERVAL);
    const parsed = raw ? parseInt(raw, 10) : DEFAULT_INTERVAL_MS;
    return clampIntervalMs(parsed);
  });

  const [portfolioRefreshEnabled, setPortfolioRefreshEnabledState] = useState<boolean>(() => {
    const val = localStorage.getItem(STORAGE_KEYS.PORTFOLIO);
    return val === null ? true : val === "1";
  });

  const [dashboardRefreshEnabled, setDashboardRefreshEnabledState] = useState<boolean>(() => {
    const val = localStorage.getItem(STORAGE_KEYS.DASHBOARD);
    return val === null ? true : val === "1";
  });

  const [signalsRefreshEnabled, setSignalsRefreshEnabledState] = useState<boolean>(() => {
    const val = localStorage.getItem(STORAGE_KEYS.SIGNALS);
    return val === null ? true : val === "1";
  });

  const [observabilityRefreshEnabled, setObservabilityRefreshEnabledState] = useState<boolean>(() => {
    const val = localStorage.getItem(STORAGE_KEYS.OBSERVABILITY);
    return val === null ? true : val === "1";
  });

  const [optionsRefreshEnabled, setOptionsRefreshEnabledState] = useState<boolean>(() => {
    const val = localStorage.getItem(STORAGE_KEYS.OPTIONS);
    return val === null ? true : val === "1";
  });

  const setAutoRefreshEnabled = (enabled: boolean) => {
    setAutoRefreshEnabledState(enabled);
    localStorage.setItem(STORAGE_KEYS.MASTER, enabled ? "1" : "0");
  };

  const setPauseWhenMarketClosed = (pause: boolean) => {
    setPauseWhenMarketClosedState(pause);
    localStorage.setItem(STORAGE_KEYS.PAUSE_CLOSED, pause ? "1" : "0");
  };

  const setAutoRefreshIntervalMs = (ms: number) => {
    const clamped = clampIntervalMs(ms);
    setAutoRefreshIntervalMsState(clamped);
    localStorage.setItem(STORAGE_KEYS.INTERVAL, String(clamped));
  };

  const setCategoryRefreshEnabled = (
    category: "portfolio" | "dashboard" | "signals" | "observability" | "options",
    enabled: boolean
  ) => {
    const valStr = enabled ? "1" : "0";
    if (category === "portfolio") {
      setPortfolioRefreshEnabledState(enabled);
      localStorage.setItem(STORAGE_KEYS.PORTFOLIO, valStr);
    } else if (category === "dashboard") {
      setDashboardRefreshEnabledState(enabled);
      localStorage.setItem(STORAGE_KEYS.DASHBOARD, valStr);
    } else if (category === "signals") {
      setSignalsRefreshEnabledState(enabled);
      localStorage.setItem(STORAGE_KEYS.SIGNALS, valStr);
    } else if (category === "observability") {
      setObservabilityRefreshEnabledState(enabled);
      localStorage.setItem(STORAGE_KEYS.OBSERVABILITY, valStr);
    } else if (category === "options") {
      setOptionsRefreshEnabledState(enabled);
      localStorage.setItem(STORAGE_KEYS.OPTIONS, valStr);
    }
  };

  const toggleAutoRefresh = () => {
    setAutoRefreshEnabled(!autoRefreshEnabled);
  };

  const isMarketOpen = computeMarketSession(new Date()) !== "CLOSED";

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
        setAutoRefreshEnabled,
        setPauseWhenMarketClosed,
        setAutoRefreshIntervalMs,
        setCategoryRefreshEnabled,
        toggleAutoRefresh,
        isMarketOpen,
      }}
    >
      {children}
    </AutoRefreshContext.Provider>
  );
};

export const useAutoRefresh = (): AutoRefreshContextValue => {
  const ctx = useContext(AutoRefreshContext);
  if (!ctx) {
    // Defensive fallback when used outside AutoRefreshProvider (e.g. isolated unit tests)
    return {
      autoRefreshEnabled: false,
      pauseWhenMarketClosed: true,
      autoRefreshIntervalMs: DEFAULT_INTERVAL_MS,
      portfolioRefreshEnabled: true,
      dashboardRefreshEnabled: true,
      signalsRefreshEnabled: true,
      observabilityRefreshEnabled: true,
      optionsRefreshEnabled: true,
      setAutoRefreshEnabled: () => {},
      setPauseWhenMarketClosed: () => {},
      setAutoRefreshIntervalMs: () => {},
      setCategoryRefreshEnabled: () => {},
      toggleAutoRefresh: () => {},
      isMarketOpen: computeMarketSession(new Date()) !== "CLOSED",
    };
  }
  return ctx;
};

import React, { createContext, useContext, useMemo } from "react";
import { useApi } from "../hooks/useApi";
import { usePoll } from "../hooks/usePoll";
import { useAutoRefresh } from "./AutoRefreshContext";
import { api } from "../api/client";

export type ExecutionMode = "LIVE" | "PAPER" | "ADVISORY" | "UNKNOWN";

interface ExecutionModeContextState {
  mode: ExecutionMode;
  advisoryOnly: boolean;
  killSwitchActive: boolean;
  loading: boolean;
  error: string | null;
  refresh: () => void;
  data: any; // Using any for brevity or import AutomationStatus
}

const ExecutionModeContext = createContext<ExecutionModeContextState | undefined>(undefined);

export const ExecutionModeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { safetyTelemetryEnabled } = useAutoRefresh();
  const { data, loading, error, reload } = useApi(api.getAutomationStatus, []);

  usePoll(reload, 30000, safetyTelemetryEnabled);

  const mode = useMemo<ExecutionMode>(() => {
    if (!data) return "UNKNOWN";
    if (data.advisory_only) return "ADVISORY";
    if (data.alpaca_paper) return "PAPER";
    return "LIVE";
  }, [data]);

  const advisoryOnly = data?.advisory_only ?? true;
  const killSwitchActive = data?.kill_switch?.active ?? false;

  const value = useMemo(
    () => ({
      mode,
      advisoryOnly,
      killSwitchActive,
      loading,
      error,
      refresh: reload,
      data,
    }),
    [mode, advisoryOnly, killSwitchActive, loading, error, reload, data]
  );

  return (
    <ExecutionModeContext.Provider value={value}>
      {children}
    </ExecutionModeContext.Provider>
  );
};

export function useExecutionMode(): ExecutionModeContextState {
  const context = useContext(ExecutionModeContext);
  if (context === undefined) {
    throw new Error("useExecutionMode must be used within an ExecutionModeProvider");
  }
  return context;
}

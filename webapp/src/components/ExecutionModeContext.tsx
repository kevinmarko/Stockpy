import { createContext, useContext, type ReactNode } from "react";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import type { AutomationStatus } from "../api/types";

// ---------------------------------------------------------------------------
// ExecutionModeContext — single source of truth for advisory/live mode state
// ---------------------------------------------------------------------------
// Fetched once from GET /automation/status on mount, then polled via
// useAutoPoll. Consumed by:
//   - AgenticTrading: prominent mode badge, gated action buttons
//   - ExecutionQueueSection: disable/enable review actions
//   - TopStatusBar: unified badge display
//
// Previously each consumer fetched /automation/status independently; this
// context eliminates redundant requests and ensures consistent state across
// every component in the same render cycle.
// ---------------------------------------------------------------------------

export interface ExecutionMode {
  /** True = advisory-only / paper mode (default, safe). False = live trading. */
  advisoryOnly: boolean;
  /** Global emergency halt — when active, no trades are placed even in live mode. */
  killSwitchActive: boolean;
  /** Kill switch trip reason, if any. */
  killSwitchReason: string | null;
  /** Dry-run flag — orders are simulated, not submitted to the broker. */
  dryRun: boolean;
  /** Alpaca paper trading flag. */
  alpacaPaper: boolean;
  /** True while the initial fetch is in flight. */
  loading: boolean;
  /** Non-null when the fetch failed. */
  error: string | null;
  /** Force a refetch (e.g. after toggling execution mode in Settings). */
  reload: () => void;
  /** Raw AutomationStatus data from the API. */
  data: AutomationStatus | null;
}

const DEFAULT: ExecutionMode = {
  advisoryOnly: true, // safe default
  killSwitchActive: false,
  killSwitchReason: null,
  dryRun: true,
  alpacaPaper: true,
  loading: true,
  error: null,
  reload: () => {},
  data: null,
};

const Ctx = createContext<ExecutionMode>(DEFAULT);

/**
 * Read the current execution mode from anywhere in the component tree.
 * Must be called inside <ExecutionModeProvider>.
 */
export function useExecutionMode(): ExecutionMode {
  return useContext(Ctx);
}

/**
 * Provider — wraps the app near the root (inside ToastProvider and
 * AutoRefreshProvider so useAutoPoll is available).
 */
export function ExecutionModeProvider({ children }: { children: ReactNode }) {
  const { data, loading, error, reload } = useApi<AutomationStatus>(
    () => api.getAutomationStatus(),
    []
  );

  useAutoPoll(reload, "portfolio", {
    enabled: !loading,
    hasError: error != null,
  });

  const value: ExecutionMode = {
    advisoryOnly: data?.advisory_only ?? true,
    killSwitchActive: data?.kill_switch?.active ?? false,
    killSwitchReason: data?.kill_switch?.reason ?? null,
    dryRun: data?.dry_run ?? true,
    alpacaPaper: data?.alpaca_paper ?? true,
    loading,
    error: error != null ? String(error) : null,
    reload,
    data: data ?? null,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

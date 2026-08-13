import type { ReactNode } from "react";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import type { AutomationStatus } from "../api/types";
import { ExecutionModeCtx, type ExecutionMode, type ExecutionModeLabel } from "../context/executionModeContext";

// ---------------------------------------------------------------------------
// ExecutionModeContext — single source of truth for advisory/live mode state
// ---------------------------------------------------------------------------
// Fetched once from GET /automation/status on mount, then polled via
// useAutoPoll. Consumed by:
//   - AgenticTrading: prominent mode badge, gated action buttons
//   - ExecutionQueueSection: disable/enable review actions
//   - TopStatusBar: unified badge display
//
// De-duplicates /automation/status for the consumers above -- previously
// each fetched it independently. NOTE: this is not (yet) a full
// de-duplication across the whole app: Settings.tsx still runs its own
// independent GET /automation/status fetch via a local useApi call, so that
// screen and this context can briefly disagree between polls. Migrate
// Settings.tsx onto useExecutionMode() to close that gap.
//
// The `ExecutionMode` type/default/context object live in
// ../context/executionModeContext.ts and the `useExecutionMode()` hook lives
// in ../hooks/useExecutionMode.ts -- both pure, non-component modules -- so
// this file only exports the `ExecutionModeProvider` component, keeping
// Vite's React Fast Refresh working here instead of invalidating on every
// edit.
// ---------------------------------------------------------------------------

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

  const mode: ExecutionModeLabel = !data
    ? "UNKNOWN"
    : data.advisory_only
    ? "ADVISORY"
    : data.alpaca_paper
    ? "PAPER"
    : "LIVE";

  const value: ExecutionMode = {
    mode,
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

  return <ExecutionModeCtx.Provider value={value}>{children}</ExecutionModeCtx.Provider>;
}

import { createContext } from "react";
import type { AutomationStatus } from "../api/types";

// ---------------------------------------------------------------------------
// ExecutionModeContext state -- split out of components/ExecutionModeContext.tsx
// (a pure, non-component module) so that file only exports the
// `ExecutionModeProvider` component and `useExecutionMode` lives in its own
// hooks/ module -- keeps Vite's React Fast Refresh working on both instead
// of invalidating on every edit. See ExecutionModeContext.tsx for the full
// consumer list and the Settings.tsx de-duplication caveat.
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

export const DEFAULT_EXECUTION_MODE: ExecutionMode = {
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

export const ExecutionModeCtx = createContext<ExecutionMode>(DEFAULT_EXECUTION_MODE);

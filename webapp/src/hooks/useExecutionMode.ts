import { useContext } from "react";
import { ExecutionModeCtx, type ExecutionMode } from "../context/executionModeContext";

/**
 * Read the current execution mode from anywhere in the component tree.
 * Must be called inside <ExecutionModeProvider> (components/ExecutionModeContext.tsx).
 */
export function useExecutionMode(): ExecutionMode {
  return useContext(ExecutionModeCtx);
}

export type { ExecutionMode };

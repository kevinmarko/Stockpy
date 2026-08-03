import { useCallback, useEffect, useState } from "react";

/**
 * usePersistedState — like useState, but backs the value to localStorage
 * so it survives page refreshes and browser restarts.
 *
 * Intended for non-sensitive UI preferences only: table column widths,
 * sort orders, collapsed/expanded panel state, dark/light mode, etc.
 * API response caching lives in api/offlineCache.ts instead.
 *
 * @param key   localStorage key (namespaced per feature, e.g. "data-explorer:sort")
 * @param defaultValue  initial value when nothing is stored
 *
 * @example
 * const [cols, setCols] = usePersistedState<string[]>("data-explorer:visible-cols", DEFAULT_COLS);
 */
export function usePersistedState<T>(
  key: string,
  defaultValue: T
): [T, (value: T | ((prev: T) => T)) => void] {
  const [state, setState] = useState<T>(() => {
    try {
      const stored = localStorage.getItem(key);
      return stored != null ? (JSON.parse(stored) as T) : defaultValue;
    } catch {
      return defaultValue;
    }
  });

  // Persist to localStorage whenever the value changes.
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(state));
    } catch {
      // localStorage full or unavailable — degrade silently.
    }
  }, [key, state]);

  // Wrap setState to support both value and updater-function forms.
  const setPersisted = useCallback(
    (value: T | ((prev: T) => T)) => {
      setState(value);
    },
    []
  );

  return [state, setPersisted];
}

import { useEffect, useState } from "react";

/**
 * useDebounce — delays updating a value until the caller stops changing it
 * for `delayMs` milliseconds. Commonly used to prevent hammering the backend
 * API on every keystroke in search / filter inputs.
 *
 * @example
 * const [raw, setRaw] = useState("");
 * const debounced = useDebounce(raw, 300);
 * // `debounced` lags `raw` by 300 ms of inactivity
 */
export function useDebounce<T>(value: T, delayMs: number = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}

import { useState, useEffect } from "react";

/**
 * Custom hook to debounce rapid value updates (e.g. search input fields).
 * Delays updating the debounced value until after `delayMs` milliseconds have passed
 * since the last time the input value changed. If `delayMs` is <= 0, updates immediately.
 */
export function useDebounce<T>(value: T, delayMs: number = 300): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value);

  useEffect(() => {
    if (delayMs <= 0) {
      setDebouncedValue(value);
      return;
    }
    const handler = setTimeout(() => {
      setDebouncedValue(value);
    }, delayMs);

    return () => {
      clearTimeout(handler);
    };
  }, [value, delayMs]);

  return debouncedValue;
}

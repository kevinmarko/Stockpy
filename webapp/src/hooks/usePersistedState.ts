import { useState, useEffect } from "react";

export function usePersistedState<T>(key: string, defaultValue: T): [T, (v: T) => void] {
  // Read from localStorage on initial render
  const [state, setState] = useState<T>(() => {
    try {
      const item = localStorage.getItem(key);
      if (item !== null) {
        return JSON.parse(item) as T;
      }
    } catch (e) {
      console.warn(`Failed to parse localStorage key "${key}"`, e);
    }
    return defaultValue;
  });

  // Write to localStorage whenever state changes
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(state));
    } catch (e) {
      console.warn(`Failed to set localStorage key "${key}"`, e);
    }
  }, [key, state]);

  return [state, setState];
}

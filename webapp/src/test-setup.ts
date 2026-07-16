// Vitest setup — extends `expect` with jest-dom matchers (toBeInTheDocument,
// toBeDisabled, etc.) and cleans up the jsdom DOM between tests so component
// tests never leak into one another.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom has no ResizeObserver; Recharts' <ResponsiveContainer> requires one.
// Without this stub, mounting any chart throws inside a passive effect, which
// React treats as an uncaught render error and unmounts the whole tree —
// producing confusing "element not found" failures unrelated to the actual
// assertion. A no-op observer is all jsdom-rendered (non-visual) tests need.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverStub;

// Node >=22.4 defines its own `localStorage`/`sessionStorage` globals (real
// Storage objects, but only when the process is started with
// `--localstorage-file`; otherwise they're `undefined` getters). Vitest's
// jsdom environment (still true as of vitest@4.1.10) only copies a window
// property onto globalThis when either the name is missing from `global` or
// it's on its hardcoded key allowlist — `localStorage`/`sessionStorage` are
// on neither, so Node's broken stub silently shadows jsdom's real, working
// Storage implementation. Wire jsdom's own window storage back onto
// globalThis so tests get the real thing instead of `undefined`.
const jsdomWindow = (globalThis as { jsdom?: { window?: Window } }).jsdom?.window;
for (const key of ["localStorage", "sessionStorage"] as const) {
  const store = jsdomWindow?.[key];
  if (store && globalThis[key] !== store) {
    Object.defineProperty(globalThis, key, {
      configurable: true,
      enumerable: true,
      get: () => store,
    });
  }
}

afterEach(() => {
  cleanup();
});

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

afterEach(() => {
  cleanup();
});

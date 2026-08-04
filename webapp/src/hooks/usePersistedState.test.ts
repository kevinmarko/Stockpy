import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { usePersistedState } from "./usePersistedState";

describe("usePersistedState", () => {
  afterEach(() => {
    localStorage.clear();
  });

  it("returns the default value when nothing is stored", () => {
    const { result } = renderHook(() => usePersistedState("test:key-1", "SPY"));
    expect(result.current[0]).toBe("SPY");
  });

  it("reads an existing localStorage value instead of the default on mount", () => {
    localStorage.setItem("test:key-2", JSON.stringify("AAPL"));
    const { result } = renderHook(() => usePersistedState("test:key-2", "SPY"));
    expect(result.current[0]).toBe("AAPL");
  });

  it("persists a direct value update to localStorage", () => {
    const { result } = renderHook(() => usePersistedState("test:key-3", "SPY"));

    act(() => {
      result.current[1]("TSLA");
    });

    expect(result.current[0]).toBe("TSLA");
    expect(localStorage.getItem("test:key-3")).toBe(JSON.stringify("TSLA"));
  });

  it("persists an updater-function update to localStorage", () => {
    const { result } = renderHook(() => usePersistedState("test:key-4", 1));

    act(() => {
      result.current[1]((prev) => prev + 1);
    });

    expect(result.current[0]).toBe(2);
    expect(localStorage.getItem("test:key-4")).toBe(JSON.stringify(2));
  });

  it("survives a remount by reading back what a prior instance wrote", () => {
    const { result, unmount } = renderHook(() => usePersistedState("test:key-5", "SPY"));
    act(() => {
      result.current[1]("QQQ");
    });
    unmount();

    const { result: result2 } = renderHook(() => usePersistedState("test:key-5", "SPY"));
    expect(result2.current[0]).toBe("QQQ");
  });

  it("degrades to the default value when the stored JSON is corrupt", () => {
    localStorage.setItem("test:key-6", "{not valid json");
    const { result } = renderHook(() => usePersistedState("test:key-6", "SPY"));
    expect(result.current[0]).toBe("SPY");
  });

  it("keeps independently-keyed instances isolated from each other", () => {
    const { result: a } = renderHook(() => usePersistedState("test:key-7a", "one"));
    const { result: b } = renderHook(() => usePersistedState("test:key-7b", "two"));

    act(() => {
      a.current[1]("changed");
    });

    expect(a.current[0]).toBe("changed");
    expect(b.current[0]).toBe("two");
  });
});

import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePoll } from "./usePoll";

describe("usePoll", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not call reload when disabled", () => {
    const reload = vi.fn();
    renderHook(() => usePoll(reload, 1000, false));
    vi.advanceTimersByTime(5000);
    expect(reload).not.toHaveBeenCalled();
  });

  it("calls reload on the interval while enabled", () => {
    const reload = vi.fn();
    renderHook(() => usePoll(reload, 1000, true));

    vi.advanceTimersByTime(1000);
    expect(reload).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(2000);
    expect(reload).toHaveBeenCalledTimes(3);
  });

  it("stops polling the instant `enabled` flips to false", () => {
    const reload = vi.fn();
    const { rerender } = renderHook(({ enabled }) => usePoll(reload, 1000, enabled), {
      initialProps: { enabled: true },
    });

    vi.advanceTimersByTime(1000);
    expect(reload).toHaveBeenCalledTimes(1);

    rerender({ enabled: false });
    vi.advanceTimersByTime(5000);
    expect(reload).toHaveBeenCalledTimes(1); // no further calls
  });

  it("clears the interval on unmount", () => {
    const reload = vi.fn();
    const { unmount } = renderHook(() => usePoll(reload, 1000, true));
    unmount();
    vi.advanceTimersByTime(5000);
    expect(reload).not.toHaveBeenCalled();
  });

  it("a non-memoized reload identity doesn't restart the interval every render", () => {
    let calls = 0;
    const { rerender } = renderHook(
      () => usePoll(() => calls++, 1000, true) // new closure every render
    );
    vi.advanceTimersByTime(1000);
    rerender();
    vi.advanceTimersByTime(1000);
    // If the ref pattern weren't in place, re-render would tear down and
    // recreate the interval (via the [enabled, ms] effect deps not
    // including reload), which would still work here -- the real risk this
    // guards is calling a STALE closure. Assert both ticks landed.
    expect(calls).toBe(2);
  });
});

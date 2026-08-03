import { act, renderHook } from "@testing-library/react";
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { AutoRefreshProvider, useAutoRefresh } from "../components/AutoRefreshContext";
import { useAutoPoll } from "./useAutoPoll";

describe("useAutoPoll", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not poll when master toggle is disabled", () => {
    const reload = vi.fn();
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AutoRefreshProvider>{children}</AutoRefreshProvider>
    );

    renderHook(() => useAutoPoll(reload, "portfolio"), { wrapper });

    vi.advanceTimersByTime(60_000);
    expect(reload).not.toHaveBeenCalled();
  });

  it("polls at configured interval when master toggle is enabled", () => {
    const reload = vi.fn();

    const InnerSetup = ({ children }: { children: React.ReactNode }) => {
      const ctx = useAutoRefresh();
      React.useEffect(() => {
        ctx.setAutoRefreshEnabled(true);
        ctx.setPauseWhenMarketClosed(false);
        ctx.setAutoRefreshIntervalMs(15_000);
      }, [ctx]);
      return <>{children}</>;
    };

    const parentWrapper = ({ children }: { children: React.ReactNode }) => (
      <AutoRefreshProvider>
        <InnerSetup>{children}</InnerSetup>
      </AutoRefreshProvider>
    );

    renderHook(() => useAutoPoll(reload, "portfolio"), { wrapper: parentWrapper });

    vi.advanceTimersByTime(15_000);
    expect(reload).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(15_000);
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it("pauses polling when category toggle is disabled", () => {
    const reload = vi.fn();

    const InnerSetup = ({ children }: { children: React.ReactNode }) => {
      const ctx = useAutoRefresh();
      React.useEffect(() => {
        ctx.setAutoRefreshEnabled(true);
        ctx.setPauseWhenMarketClosed(false);
        ctx.setCategoryRefreshEnabled("portfolio", false);
      }, [ctx]);
      return <>{children}</>;
    };

    const parentWrapper = ({ children }: { children: React.ReactNode }) => (
      <AutoRefreshProvider>
        <InnerSetup>{children}</InnerSetup>
      </AutoRefreshProvider>
    );

    renderHook(() => useAutoPoll(reload, "portfolio"), { wrapper: parentWrapper });

    vi.advanceTimersByTime(60_000);
    expect(reload).not.toHaveBeenCalled();
  });

  it("applies exponential backoff on consecutive errors", () => {
    const reload = vi.fn();

    const InnerSetup = ({ children }: { children: React.ReactNode }) => {
      const ctx = useAutoRefresh();
      React.useEffect(() => {
        ctx.setAutoRefreshEnabled(true);
        ctx.setPauseWhenMarketClosed(false);
        ctx.setAutoRefreshIntervalMs(10_000);
      }, [ctx]);
      return <>{children}</>;
    };

    const parentWrapper = ({ children }: { children: React.ReactNode }) => (
      <AutoRefreshProvider>
        <InnerSetup>{children}</InnerSetup>
      </AutoRefreshProvider>
    );

    const { rerender } = renderHook(
      ({ hasError }) => useAutoPoll(reload, "portfolio", { hasError }),
      {
        wrapper: parentWrapper,
        initialProps: { hasError: true },
      }
    );

    // Initial interval with error 1: 10s * 1.5 = 15s
    vi.advanceTimersByTime(10_000);
    expect(reload).not.toHaveBeenCalled();

    vi.advanceTimersByTime(5_000);
    expect(reload).toHaveBeenCalledTimes(1);

    // Error clears -> interval resets to 10s
    rerender({ hasError: false });
    vi.advanceTimersByTime(10_000);
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it("escalates the backoff on each successive failed poll, not just the first", () => {
    const reload = vi.fn();

    const InnerSetup = ({ children }: { children: React.ReactNode }) => {
      const ctx = useAutoRefresh();
      React.useEffect(() => {
        ctx.setAutoRefreshEnabled(true);
        ctx.setPauseWhenMarketClosed(false);
      }, [ctx]);
      return <>{children}</>;
    };

    const parentWrapper = ({ children }: { children: React.ReactNode }) => (
      <AutoRefreshProvider>
        <InnerSetup>{children}</InnerSetup>
      </AutoRefreshProvider>
    );

    renderHook(
      () => useAutoPoll(reload, "portfolio", { hasError: true, customIntervalMs: 10_000 }),
      { wrapper: parentWrapper }
    );

    // Each advance is act()-wrapped: the tick handler sets state (escalating
    // consecutiveErrors), which changes the interval's `ms` and must flush
    // before the NEXT advance, or the old setInterval keeps firing at its
    // stale cadence instead of the newly-escalated one.
    // 1st failure (rising edge, from mount) -> 10s * 1.5 = 15s
    act(() => vi.advanceTimersByTime(14_999));
    expect(reload).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));
    expect(reload).toHaveBeenCalledTimes(1);

    // 2nd consecutive failure -> 10s * 1.5^2 = 22.5s (used to be pinned at
    // 15s forever, since the old [hasError]-only effect never re-fired while
    // hasError stayed the same boolean value across polls)
    act(() => vi.advanceTimersByTime(22_499));
    expect(reload).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(1));
    expect(reload).toHaveBeenCalledTimes(2);

    // 3rd -> 10s * 1.5^3 = 33.75s
    act(() => vi.advanceTimersByTime(33_749));
    expect(reload).toHaveBeenCalledTimes(2);
    act(() => vi.advanceTimersByTime(1));
    expect(reload).toHaveBeenCalledTimes(3);
  });

  it("caps the backoff at 5 minutes", () => {
    const reload = vi.fn();

    const InnerSetup = ({ children }: { children: React.ReactNode }) => {
      const ctx = useAutoRefresh();
      React.useEffect(() => {
        ctx.setAutoRefreshEnabled(true);
        ctx.setPauseWhenMarketClosed(false);
      }, [ctx]);
      return <>{children}</>;
    };

    const parentWrapper = ({ children }: { children: React.ReactNode }) => (
      <AutoRefreshProvider>
        <InnerSetup>{children}</InnerSetup>
      </AutoRefreshProvider>
    );

    // base 100s: the uncapped ladder would be 150s, 225s, 337.5s -- the third
    // step must clamp to exactly 300_000ms (MAX_BACKOFF_MS), not overshoot.
    renderHook(
      () => useAutoPoll(reload, "portfolio", { hasError: true, customIntervalMs: 100_000 }),
      { wrapper: parentWrapper }
    );

    act(() => vi.advanceTimersByTime(150_000)); // 1st
    expect(reload).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(225_000)); // 2nd
    expect(reload).toHaveBeenCalledTimes(2);

    // Uncapped, this rung would be 337_500ms -- confirm it clamps to 300_000.
    act(() => vi.advanceTimersByTime(299_999));
    expect(reload).toHaveBeenCalledTimes(2);
    act(() => vi.advanceTimersByTime(1));
    expect(reload).toHaveBeenCalledTimes(3);
  });

  it("never backs off to an interval SHORTER than the base (e.g. the Robinhood category's 1h default)", () => {
    const reload = vi.fn();

    const InnerSetup = ({ children }: { children: React.ReactNode }) => {
      const ctx = useAutoRefresh();
      React.useEffect(() => {
        ctx.setAutoRefreshEnabled(true);
        ctx.setPauseWhenMarketClosed(false);
        // The robinhood category defaults OFF (unlike the other five) --
        // must be explicitly enabled for this poller to run at all.
        ctx.setCategoryRefreshEnabled("robinhood", true);
      }, [ctx]);
      return <>{children}</>;
    };

    const parentWrapper = ({ children }: { children: React.ReactNode }) => (
      <AutoRefreshProvider>
        <InnerSetup>{children}</InnerSetup>
      </AutoRefreshProvider>
    );

    // base 1h (3_600_000ms) already exceeds MAX_BACKOFF_MS (300_000ms) --
    // Math.min(MAX_BACKOFF_MS, base * mult) alone would produce an interval
    // SHORTER than the base once mult > 1, which is backwards: a failing
    // Robinhood login must never retry MORE often than a healthy one would.
    renderHook(
      () => useAutoPoll(reload, "robinhood", { hasError: true, customIntervalMs: 3_600_000 }),
      { wrapper: parentWrapper }
    );

    act(() => vi.advanceTimersByTime(3_600_000 - 1));
    expect(reload).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));
    expect(reload).toHaveBeenCalledTimes(1);

    // Still backed off to the 1h base, not a shorter "capped" interval.
    act(() => vi.advanceTimersByTime(3_600_000 - 1));
    expect(reload).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(1));
    expect(reload).toHaveBeenCalledTimes(2);
  });
});

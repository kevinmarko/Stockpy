import { renderHook } from "@testing-library/react";
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
});

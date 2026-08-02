import { renderHook, act } from "@testing-library/react";
import React from "react";
import { describe, it, expect, beforeEach } from "vitest";
import {
  AutoRefreshProvider,
  useAutoRefresh,
  clampIntervalMs,
} from "./AutoRefreshContext";

describe("AutoRefreshContext", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("clamps interval values between 5s and 24h", () => {
    expect(clampIntervalMs(1000)).toBe(5000);
    expect(clampIntervalMs(100_000_000)).toBe(86_400_000);
    expect(clampIntervalMs(NaN)).toBe(30_000);
    expect(clampIntervalMs(30_000)).toBe(30_000);
  });

  it("provides default state and updates master toggle", () => {
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AutoRefreshProvider>{children}</AutoRefreshProvider>
    );

    const { result } = renderHook(() => useAutoRefresh(), { wrapper });

    expect(result.current.autoRefreshEnabled).toBe(false);
    expect(result.current.pauseWhenMarketClosed).toBe(true);
    expect(result.current.autoRefreshIntervalMs).toBe(30_000);

    act(() => {
      result.current.setAutoRefreshEnabled(true);
    });

    expect(result.current.autoRefreshEnabled).toBe(true);
    expect(localStorage.getItem("stockpy.auto_refresh.enabled")).toBe("1");
  });

  it("updates custom interval and clamps correctly", () => {
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AutoRefreshProvider>{children}</AutoRefreshProvider>
    );

    const { result } = renderHook(() => useAutoRefresh(), { wrapper });

    act(() => {
      result.current.setAutoRefreshIntervalMs(45_000);
    });

    expect(result.current.autoRefreshIntervalMs).toBe(45_000);
    expect(localStorage.getItem("stockpy.auto_refresh.interval_ms")).toBe("45000");

    act(() => {
      result.current.setAutoRefreshIntervalMs(2_000); // Below 5s min limit
    });

    expect(result.current.autoRefreshIntervalMs).toBe(5_000);
    expect(localStorage.getItem("stockpy.auto_refresh.interval_ms")).toBe("5000");
  });

  it("updates category toggles independently", () => {
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AutoRefreshProvider>{children}</AutoRefreshProvider>
    );

    const { result } = renderHook(() => useAutoRefresh(), { wrapper });

    expect(result.current.portfolioRefreshEnabled).toBe(true);
    expect(result.current.signalsRefreshEnabled).toBe(true);

    act(() => {
      result.current.setCategoryRefreshEnabled("portfolio", false);
    });

    expect(result.current.portfolioRefreshEnabled).toBe(false);
    expect(result.current.signalsRefreshEnabled).toBe(true);
    expect(localStorage.getItem("stockpy.auto_refresh.portfolio_enabled")).toBe("0");
  });

  it("provides defensive fallback when used outside provider", () => {
    const { result } = renderHook(() => useAutoRefresh());
    expect(result.current.autoRefreshEnabled).toBe(false);
    expect(typeof result.current.setAutoRefreshEnabled).toBe("function");
  });
});

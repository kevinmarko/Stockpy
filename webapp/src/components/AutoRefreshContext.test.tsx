// This file touches Node's fs/path APIs for ONE regression test (the
// source-scan guard at the bottom) -- see theme.test.ts for the same pattern
// and rationale. The app's tsconfig deliberately keeps Node globals out of
// browser code via an explicit `types` allowlist, so pull the node types in
// for THIS FILE ONLY via a reference directive.
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, renderHook, act } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AutoRefreshProvider,
  useAutoRefresh,
  clampIntervalMs,
  clampCategoryIntervalMs,
} from "./AutoRefreshContext";

function wrapper({ children }: { children: React.ReactNode }) {
  return <AutoRefreshProvider>{children}</AutoRefreshProvider>;
}

describe("AutoRefreshContext", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("clamps interval values between 5s and 24h", () => {
    expect(clampIntervalMs(1000)).toBe(5000);
    expect(clampIntervalMs(100_000_000)).toBe(86_400_000);
    expect(clampIntervalMs(NaN)).toBe(30_000);
    expect(clampIntervalMs(30_000)).toBe(30_000);
  });

  it("provides default state and updates master toggle", () => {
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

  // --- 1b: guarded localStorage -----------------------------------------

  it("localStorage throwing on every access still renders with the documented defaults, not a blank app", () => {
    const throwing = () => {
      throw new DOMException("blocked by policy", "SecurityError");
    };
    try {
      vi.spyOn(localStorage, "getItem").mockImplementation(throwing);
      vi.spyOn(localStorage, "setItem").mockImplementation(throwing);
    } catch {
      vi.spyOn(Storage.prototype, "getItem").mockImplementation(throwing);
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(throwing);
    }

    const { result } = renderHook(() => useAutoRefresh(), { wrapper });

    expect(result.current.autoRefreshEnabled).toBe(false);
    expect(result.current.pauseWhenMarketClosed).toBe(true);
    expect(result.current.autoRefreshIntervalMs).toBe(30_000);
    expect(result.current.portfolioRefreshEnabled).toBe(true);
    expect(result.current.dashboardRefreshEnabled).toBe(true);
    expect(result.current.signalsRefreshEnabled).toBe(true);
    expect(result.current.observabilityRefreshEnabled).toBe(true);
    expect(result.current.optionsRefreshEnabled).toBe(true);
    expect(result.current.robinhoodRefreshEnabled).toBe(false);
    expect(result.current.safetyTelemetryEnabled).toBe(true);
    expect(result.current.isTabVisible).toBe(true);

    // Setters must not throw either -- a write failure is swallowed, not
    // fatal.
    expect(() => {
      act(() => {
        result.current.setAutoRefreshEnabled(true);
        result.current.setCategoryIntervalMs("robinhood", 1_000_000);
      });
    }).not.toThrow();
    expect(result.current.autoRefreshEnabled).toBe(true);
  });

  // --- 1e: isMarketOpen on a 60s timer ------------------------------------

  it("re-evaluates isMarketOpen on a 60s timer instead of freezing at mount", () => {
    vi.useFakeTimers();
    // 2026-07-16 is a Thursday. 07:59 UTC = 03:59 ET (summer, UTC-4) --
    // before the 4am ET CLOSED/PRE-POST boundary.
    vi.setSystemTime(new Date("2026-07-16T07:59:00Z"));

    const { result } = renderHook(() => useAutoRefresh(), { wrapper });
    expect(result.current.isMarketOpen).toBe(false);

    act(() => {
      // 08:01 UTC = 04:01 ET -- just past the boundary.
      vi.setSystemTime(new Date("2026-07-16T08:01:00Z"));
      vi.advanceTimersByTime(60_000);
    });

    expect(result.current.isMarketOpen).toBe(true);
  });

  // --- 1f: stable setter identity -----------------------------------------

  it("setter identity is stable across re-renders (useCallback, not recreated every render)", () => {
    const { result, rerender } = renderHook(() => useAutoRefresh(), { wrapper });

    const capturedSetInterval = result.current.setAutoRefreshIntervalMs;
    const capturedSetCategory = result.current.setCategoryRefreshEnabled;

    act(() => {
      result.current.setPauseWhenMarketClosed(false); // unrelated state change
    });
    rerender();

    expect(result.current.setAutoRefreshIntervalMs).toBe(capturedSetInterval);
    expect(result.current.setCategoryRefreshEnabled).toBe(capturedSetCategory);
  });

  // --- safetyTelemetryEnabled: a third top-level switch --------------------

  it("safetyTelemetryEnabled defaults true and toggles independently of the master switch", () => {
    const { result } = renderHook(() => useAutoRefresh(), { wrapper });

    expect(result.current.safetyTelemetryEnabled).toBe(true);
    expect(result.current.autoRefreshEnabled).toBe(false);

    act(() => {
      result.current.setSafetyTelemetryEnabled(false);
    });

    expect(result.current.safetyTelemetryEnabled).toBe(false);
    expect(result.current.autoRefreshEnabled).toBe(false); // untouched
    expect(localStorage.getItem("stockpy.auto_refresh.safety_telemetry_enabled")).toBe("0");
  });

  it("safetyTelemetryEnabled defaults true in the out-of-provider fallback too", () => {
    const { result } = renderHook(() => useAutoRefresh());
    expect(result.current.safetyTelemetryEnabled).toBe(true);
  });

  // --- isTabVisible: single app-wide subscription -------------------------

  it("registers exactly one visibilitychange listener app-wide, regardless of how many consumers read the context", () => {
    const addSpy = vi.spyOn(document, "addEventListener");

    function Consumer() {
      useAutoRefresh();
      return null;
    }

    render(
      <AutoRefreshProvider>
        <Consumer />
        <Consumer />
        <Consumer />
      </AutoRefreshProvider>
    );

    const visibilityListeners = addSpy.mock.calls.filter(([type]) => type === "visibilitychange");
    expect(visibilityListeners).toHaveLength(1);
  });

  it("isTabVisible defaults true in the out-of-provider fallback too", () => {
    const { result } = renderHook(() => useAutoRefresh());
    expect(result.current.isTabVisible).toBe(true);
  });

  // --- robinhood category: deliberately asymmetric default ----------------

  it("the robinhood category defaults OFF, unlike every other category", () => {
    const { result } = renderHook(() => useAutoRefresh(), { wrapper });

    expect(result.current.robinhoodRefreshEnabled).toBe(false);
    expect(result.current.portfolioRefreshEnabled).toBe(true);
    expect(result.current.dashboardRefreshEnabled).toBe(true);
    expect(result.current.signalsRefreshEnabled).toBe(true);
    expect(result.current.observabilityRefreshEnabled).toBe(true);
    expect(result.current.optionsRefreshEnabled).toBe(true);

    act(() => {
      result.current.setCategoryRefreshEnabled("robinhood", true);
    });

    expect(result.current.robinhoodRefreshEnabled).toBe(true);
    expect(localStorage.getItem("stockpy.auto_refresh.robinhood_enabled")).toBe("1");
  });

  it("robinhoodRefreshEnabled defaults false in the out-of-provider fallback too", () => {
    const { result } = renderHook(() => useAutoRefresh());
    expect(result.current.robinhoodRefreshEnabled).toBe(false);
  });

  // --- per-category interval override --------------------------------------

  it("resolveIntervalMs falls back to the global interval when no category, or a category with no override, is given", () => {
    const { result } = renderHook(() => useAutoRefresh(), { wrapper });

    expect(result.current.resolveIntervalMs()).toBe(30_000);
    expect(result.current.resolveIntervalMs("portfolio")).toBe(30_000);

    act(() => {
      result.current.setAutoRefreshIntervalMs(60_000);
    });
    expect(result.current.resolveIntervalMs("dashboard")).toBe(60_000);
  });

  it("clamps a hand-planted out-of-range robinhood interval to the 15 min floor on READ", () => {
    // Simulates a devtools-edited localStorage value below the floor.
    localStorage.setItem("stockpy.auto_refresh.robinhood_interval_ms", "1000");

    const { result } = renderHook(() => useAutoRefresh(), { wrapper });

    expect(result.current.resolveIntervalMs("robinhood")).toBe(900_000);
    expect(clampCategoryIntervalMs("robinhood", 1000)).toBe(900_000);
  });

  it("setCategoryIntervalMs clamps to the 15 min floor on WRITE too", () => {
    const { result } = renderHook(() => useAutoRefresh(), { wrapper });

    act(() => {
      result.current.setCategoryIntervalMs("robinhood", 60_000); // 1 min -- below floor
    });

    expect(result.current.categoryIntervalMs.robinhood).toBe(900_000);
    expect(result.current.resolveIntervalMs("robinhood")).toBe(900_000);
    expect(localStorage.getItem("stockpy.auto_refresh.robinhood_interval_ms")).toBe("900000");
  });

  it("setCategoryIntervalMs accepts a value above the floor unchanged", () => {
    const { result } = renderHook(() => useAutoRefresh(), { wrapper });

    act(() => {
      result.current.setCategoryIntervalMs("robinhood", 7_200_000); // 2h
    });

    expect(result.current.resolveIntervalMs("robinhood")).toBe(7_200_000);
  });

  it("resolveIntervalMs / setCategoryIntervalMs are no-ops in the out-of-provider fallback", () => {
    const { result } = renderHook(() => useAutoRefresh());
    expect(result.current.resolveIntervalMs("robinhood")).toBe(30_000);
    expect(() => result.current.setCategoryIntervalMs("robinhood", 1000)).not.toThrow();
  });
});

describe("AutoRefreshContext source guard", () => {
  it("never re-imports computeMarketSession from ./TopStatusBar (regression: the context used to drag the whole status-bar component graph -- api/client, useApi, Modal, ToastContext -- into every screen in the app)", () => {
    const src = readFileSync(
      resolve(process.cwd(), "src/components/AutoRefreshContext.tsx"),
      "utf-8"
    );
    expect(src).not.toMatch(/from ["']\.\/TopStatusBar["']/);
  });
});

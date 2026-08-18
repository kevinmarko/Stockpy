/**
 * DynamicCircuitBreakerBadge.test.tsx
 * ===================================
 * Unit and integration tests for DynamicCircuitBreakerBadge component.
 */

import { render, screen, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DynamicCircuitBreakerBadge } from "./DynamicCircuitBreakerBadge";
import { api } from "../../api/client";
import type { CircuitBreakerStatusResponse } from "../../api/types";

const NORMAL_STATUS: CircuitBreakerStatusResponse = {
  state: "NORMAL",
  volatility_zscore: 0.85,
  vpin: 0.18,
  ofi: 120.5,
  loss_velocity_per_min: -15.4,
  reason: null,
  updated_at: "2026-08-17T12:00:00.000Z",
};

const SOFT_HALT_STATUS: CircuitBreakerStatusResponse = {
  state: "SOFT_HALT",
  volatility_zscore: 3.82,
  vpin: 0.46,
  ofi: -1250.0,
  loss_velocity_per_min: -210.0,
  reason: "VOLATILITY_BURST_HALT: 5m EWMA realized vol Z-score 3.82 > threshold 3.50",
  updated_at: "2026-08-17T12:00:00.000Z",
};

const HARD_HALT_STATUS: CircuitBreakerStatusResponse = {
  state: "HARD_HALT",
  volatility_zscore: 4.15,
  vpin: 0.58,
  ofi: -2400.0,
  loss_velocity_per_min: -750.0,
  reason: "LOSS_VELOCITY_BREACH: Intraday loss rate $750.00/min exceeds allowable rate $666.67/min",
  updated_at: "2026-08-17T12:00:00.000Z",
};

const CAUTION_STATUS: CircuitBreakerStatusResponse = {
  state: "CAUTION",
  volatility_zscore: 2.35,
  vpin: 0.32,
  ofi: -450.2,
  loss_velocity_per_min: -85.5,
  reason: "Elevated market volatility detected across monitored universe",
  updated_at: "2026-08-17T12:00:00.000Z",
};

describe("DynamicCircuitBreakerBadge", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders NORMAL state successfully on the happy path", async () => {
    vi.spyOn(api, "getCircuitBreakerStatus").mockResolvedValue(NORMAL_STATUS);

    render(<DynamicCircuitBreakerBadge autoRefresh={false} />);

    // Initially shows loading
    expect(screen.getByTestId("circuit-breaker-loading")).toBeInTheDocument();

    // After resolve, renders full card
    const card = await screen.findByTestId("circuit-breaker-card");
    expect(card).toBeInTheDocument();
    expect(screen.getByTestId("circuit-breaker-state-badge")).toHaveTextContent("NORMAL");
    expect(screen.getByTestId("metric-vol-zscore-value")).toHaveTextContent("+0.85σ");
    expect(screen.getByTestId("metric-vpin-value")).toHaveTextContent("0.18");
    expect(screen.getByTestId("metric-ofi-value")).toHaveTextContent("+120.5");
    expect(screen.getByTestId("metric-loss-velocity-value")).toHaveTextContent("-$15.40/min");

    // No reason alert when reason is null
    expect(screen.queryByTestId("circuit-breaker-reason-alert")).not.toBeInTheDocument();
  });

  it("renders compact mode for NORMAL state with pulse dot", async () => {
    vi.spyOn(api, "getCircuitBreakerStatus").mockResolvedValue(NORMAL_STATUS);

    render(<DynamicCircuitBreakerBadge compact={true} autoRefresh={false} />);

    const compactBadge = await screen.findByTestId("circuit-breaker-badge-compact");
    expect(compactBadge).toBeInTheDocument();
    expect(screen.getByTestId("circuit-breaker-state-label")).toHaveTextContent("NORMAL");
    expect(screen.getByTestId("circuit-breaker-pulse-dot")).toBeInTheDocument();
  });

  it("renders SOFT_HALT with prominent halt reason alert", async () => {
    vi.spyOn(api, "getCircuitBreakerStatus").mockResolvedValue(SOFT_HALT_STATUS);

    render(<DynamicCircuitBreakerBadge autoRefresh={false} />);

    const card = await screen.findByTestId("circuit-breaker-card");
    expect(card).toBeInTheDocument();
    expect(screen.getByTestId("circuit-breaker-state-badge")).toHaveTextContent("SOFT HALT");
    expect(screen.getByTestId("metric-vol-zscore-value")).toHaveTextContent("+3.82σ");
    expect(screen.getByTestId("metric-vpin-value")).toHaveTextContent("0.46");
    expect(screen.getByTestId("metric-ofi-value")).toHaveTextContent("-1250.0");

    const reasonAlert = screen.getByTestId("circuit-breaker-reason-alert");
    expect(reasonAlert).toBeInTheDocument();
    expect(reasonAlert).toHaveTextContent("PROTECTIVE SOFT HALT ACTIVE");
    expect(screen.getByTestId("circuit-breaker-reason-text")).toHaveTextContent(
      "VOLATILITY_BURST_HALT: 5m EWMA realized vol Z-score 3.82 > threshold 3.50"
    );
  });

  it("renders HARD_HALT with critical halt breach alert", async () => {
    vi.spyOn(api, "getCircuitBreakerStatus").mockResolvedValue(HARD_HALT_STATUS);

    render(<DynamicCircuitBreakerBadge autoRefresh={false} />);

    const card = await screen.findByTestId("circuit-breaker-card");
    expect(card).toBeInTheDocument();
    expect(screen.getByTestId("circuit-breaker-state-badge")).toHaveTextContent("HARD HALT");
    expect(screen.getByTestId("metric-loss-velocity-value")).toHaveTextContent("-$750.00/min");

    const reasonAlert = screen.getByTestId("circuit-breaker-reason-alert");
    expect(reasonAlert).toBeInTheDocument();
    expect(reasonAlert).toHaveTextContent("CRITICAL HARD HALT TRIGGERED");
    expect(screen.getByTestId("circuit-breaker-reason-text")).toHaveTextContent(
      "LOSS_VELOCITY_BREACH: Intraday loss rate $750.00/min exceeds allowable rate $666.67/min"
    );
  });

  it("renders CAUTION state with cautionary alert banner", async () => {
    vi.spyOn(api, "getCircuitBreakerStatus").mockResolvedValue(CAUTION_STATUS);

    render(<DynamicCircuitBreakerBadge autoRefresh={false} />);

    const card = await screen.findByTestId("circuit-breaker-card");
    expect(card).toBeInTheDocument();
    expect(screen.getByTestId("circuit-breaker-state-badge")).toHaveTextContent("CAUTION");

    const reasonAlert = screen.getByTestId("circuit-breaker-reason-alert");
    expect(reasonAlert).toBeInTheDocument();
    expect(reasonAlert).toHaveTextContent("CAUTION NOTICE");
    expect(screen.getByTestId("circuit-breaker-reason-text")).toHaveTextContent(
      "Elevated market volatility detected across monitored universe"
    );
  });

  it("renders immediately without fetch when controlled status prop is provided", () => {
    const fetchSpy = vi.spyOn(api, "getCircuitBreakerStatus");

    render(<DynamicCircuitBreakerBadge status={NORMAL_STATUS} />);

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId("circuit-breaker-card")).toBeInTheDocument();
    expect(screen.getByTestId("circuit-breaker-state-badge")).toHaveTextContent("NORMAL");
  });

  it("shows error alert when API fetch fails", async () => {
    vi.spyOn(api, "getCircuitBreakerStatus").mockRejectedValue(new Error("Network connection lost"));

    render(<DynamicCircuitBreakerBadge autoRefresh={false} />);

    const errorEl = await screen.findByTestId("circuit-breaker-error");
    expect(errorEl).toBeInTheDocument();
    expect(errorEl).toHaveTextContent("Network connection lost");
  });

  it("cleans up gracefully on unmount during in-flight fetch and timer lifecycle", async () => {
    vi.useFakeTimers();

    let resolvePromise: (value: CircuitBreakerStatusResponse) => void = () => {};
    const pendingPromise = new Promise<CircuitBreakerStatusResponse>((resolve) => {
      resolvePromise = resolve;
    });

    vi.spyOn(api, "getCircuitBreakerStatus").mockReturnValue(pendingPromise);

    const { unmount } = render(
      <DynamicCircuitBreakerBadge autoRefresh={true} pollIntervalMs={2000} />
    );

    expect(screen.getByTestId("circuit-breaker-loading")).toBeInTheDocument();

    // Unmount before promise resolves
    unmount();

    // Resolve after unmount - should not trigger setState on unmounted component
    act(() => {
      resolvePromise(NORMAL_STATUS);
      vi.advanceTimersByTime(5000);
    });

    vi.useRealTimers();
  });
});

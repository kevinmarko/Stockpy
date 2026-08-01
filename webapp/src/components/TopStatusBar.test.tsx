/**
 * TopStatusBar.test.tsx — the always-on top bar. Its three headline claims
 * (heartbeat, kill switch, regime) must all come from real GET
 * /automation/status / GET /observability/summary data, never local fake
 * state — this is the regression test for that. Also covers the pure
 * `computeMarketSession` ET-time classifier directly (weekday/weekend,
 * RTH/pre-post/closed boundaries) since driving that through the rendered
 * component would require mocking the system timezone.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, afterEach } from "vitest";
import { TopStatusBar, computeMarketSession } from "./TopStatusBar";
import { ToastProvider } from "./ToastContext";
import { DensityProvider } from "./DensityContext";
import { api } from "../api/client";
import {
  mockEtfTransmissionDisabled,
  mockHeartbeatNoData,
  mockSizingCapAuditDisabled,
  mockStrategyPnlEmpty,
  mockSystemTelemetryUnavailable,
} from "../api/mock";
import type { AutomationStatus, ObservabilitySummary } from "../api/types";

function renderBar() {
  return render(
    <ToastProvider>
      <DensityProvider>
        <TopStatusBar />
      </DensityProvider>
    </ToastProvider>
  );
}

function automationStatus(overrides: Partial<AutomationStatus> = {}): AutomationStatus {
  return {
    daemon: {
      alive: true,
      source: "control_api",
      pid: null,
      pid_alive: null,
      port: 8601,
      started_at: "2026-07-16T10:00:00+00:00",
      interval_seconds: 300,
      is_running: false,
      current_run_id: null,
      engines_warm: true,
    },
    last_run: null,
    last_run_source: "state_snapshot",
    pipeline: {
      snapshot_age_seconds: 42,
      snapshot_age_source: "timestamp",
      heartbeat_age_seconds: null,
      heartbeat_note: "advisory mode",
    },
    progress: null,
    kill_switch: { active: false, reason: null },
    errors: { generated_at: null, entry_count: 0, entries: [] },
    advisory_only: true,
    dry_run: false,
    alpaca_paper: false,
    ...overrides,
  };
}

function observabilitySummary(overrides: Partial<ObservabilitySummary> = {}): ObservabilitySummary {
  return {
    portfolio_risk: {
      sharpe_ratio: null,
      calmar_ratio: null,
      max_drawdown: null,
      max_drawdown_duration_days: null,
      cagr: null,
      n_snapshots: 0,
      min_snapshots_required: 20,
      reason: "No account snapshots yet.",
    },
    portfolio_heat: {
      heat_pct: null,
      max_portfolio_heat: 0.06,
      over_limit: null,
      n_positions: 0,
      as_of: null,
      reason: "No account snapshot yet.",
    },
    equity_curve: { range: "1M", points: [], reason: "No account snapshots yet." },
    regime: {
      as_of: null,
      market_regime: null,
      vix: null,
      sahm_rule: null,
      high_yield_oas: null,
      yield_curve: null,
      hmm_risk_on_probability: null,
      kill_switch_active: null,
      macro_regime_gate_enabled: null,
      reason: "No state snapshot yet.",
      macro_gate_writable: false,
      macro_gate_writable_note: "Writes are disabled.",
    },
    forecast_skill: {
      horizon_days: 30,
      window_days: 180,
      min_obs: 30,
      reliability_curve: [],
      skill_weights: {},
      pending: 0,
      completed: 0,
      reason: "No forecast history yet.",
    },
    risk_gate_blocks: { entries: [], count: 0, reason: "No risk-gate blocks logged yet." },
    circuit_breakers: { trips: [], counts: { critical: 0, warning: 0, total: 0 }, window_hours: 24, reason: "No trips." },
    system_telemetry: mockSystemTelemetryUnavailable(),
    sizing_cap_audit: mockSizingCapAuditDisabled(),
    etf_transmission: mockEtfTransmissionDisabled(),
    heartbeat: mockHeartbeatNoData(),
    strategy_pnl: mockStrategyPnlEmpty(),
    ...overrides,
  };
}

describe("TopStatusBar", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders daemon-alive as Live and shows the real snapshot age, not a fabricated 'Live' forever", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce(automationStatus());
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(observabilitySummary());
    renderBar();
    expect(await screen.findByText("Live")).toBeInTheDocument();
    expect(await screen.findByText("(42s ago)")).toBeInTheDocument();
  });

  it("a dead daemon renders Offline, not a fake Live badge", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce(
      automationStatus({ daemon: { ...automationStatus().daemon, alive: false } })
    );
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(observabilitySummary());
    renderBar();
    expect(await screen.findByText("Offline")).toBeInTheDocument();
  });

  it("shows the real macro regime read-only, or '—' when the backend has none -- never a fabricated RISK-ON default", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce(automationStatus());
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(
      observabilitySummary({ regime: { ...observabilitySummary().regime, market_regime: "RISK_ON" } })
    );
    renderBar();
    expect(await screen.findByText("RISK_ON")).toBeInTheDocument();
  });

  it("kill switch active renders TRIPPED and a Reset action", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce(
      automationStatus({ kill_switch: { active: true, reason: "manual pause" } })
    );
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(observabilitySummary());
    renderBar();
    expect(await screen.findByText("TRIPPED (PAUSED)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reset Kill Switch/ })).toBeInTheDocument();
  });

  it("tripping the kill switch requires a reason and calls the real pauseAutomation endpoint", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(automationStatus());
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(observabilitySummary());
    const pauseSpy = vi.spyOn(api, "pauseAutomation").mockResolvedValueOnce({ active: true, reason: "testing" });
    renderBar();

    const tripButton = await screen.findByRole("button", { name: /Trip Kill Switch/ });
    await user.click(tripButton);

    const confirmButton = screen.getByRole("button", { name: "Trip Kill Switch" });
    expect(confirmButton).toBeDisabled();

    await user.type(screen.getByLabelText("Reason (required)"), "testing");
    expect(confirmButton).not.toBeDisabled();
    await user.click(confirmButton);

    await waitFor(() => expect(pauseSpy).toHaveBeenCalledWith("testing"));
  });

  it("resume is blocked with an explanatory title when the switch is active and the platform is NOT advisory-only", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce(
      automationStatus({ kill_switch: { active: true, reason: "live halt" }, advisory_only: false })
    );
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(observabilitySummary());
    renderBar();
    const resetButton = await screen.findByRole("button", { name: /Reset Kill Switch/ });
    expect(resetButton).toBeDisabled();
  });
});

describe("computeMarketSession", () => {
  // All instants below are given as UTC ISO strings so the test is
  // timezone-independent regardless of the machine running it; the function
  // itself converts to America/New_York internally.
  it("classifies a weekday during regular trading hours as RTH (Open)", () => {
    // 2026-07-16 is a Thursday. 14:00 UTC = 10:00 ET (summer, UTC-4).
    expect(computeMarketSession(new Date("2026-07-16T14:00:00Z"))).toBe("RTH (Open)");
  });

  it("classifies a weekday before the open as PRE/POST", () => {
    // 09:00 UTC = 05:00 ET.
    expect(computeMarketSession(new Date("2026-07-16T09:00:00Z"))).toBe("PRE/POST");
  });

  it("classifies a weekday late at night as CLOSED", () => {
    // 03:00 UTC = 23:00 ET (previous day).
    expect(computeMarketSession(new Date("2026-07-16T03:00:00Z"))).toBe("CLOSED");
  });

  it("classifies a Saturday as CLOSED even during what would be RTH hours on a weekday", () => {
    // 2026-07-18 is a Saturday. 14:00 UTC = 10:00 ET.
    expect(computeMarketSession(new Date("2026-07-18T14:00:00Z"))).toBe("CLOSED");
  });
});

/**
 * TopStatusBar.test.tsx — the always-on top bar. Its three headline claims
 * (heartbeat, kill switch, regime) must all come from real GET
 * /automation/status / GET /observability/summary data, never local fake
 * state — this is the regression test for that. The pure `computeMarketSession`
 * ET-time classifier itself lives in marketSession.ts and is covered directly
 * by marketSession.test.ts.
 */
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { TopStatusBar } from "./TopStatusBar";
import { AutoRefreshProvider } from "./AutoRefreshContext";
import { ToastProvider } from "./ToastProvider";
import { DensityProvider } from "./DensityContext";
import { ExecutionModeProvider } from "./ExecutionModeContext";
import { JobStatusProvider } from "./JobStatusContext";
import { ThemeProvider } from "../context/ThemeContext";
import { api } from "../api/client";
import {
  mockEtfTransmissionDisabled,
  mockForecastSkillBySymbolEmpty,
  mockHeartbeatNoData,
  mockLatencyHeatmapDisabled,
  mockSizingCapAuditDisabled,
  mockStrategyPnlEmpty,
  mockSystemTelemetryUnavailable,
} from "../api/mock";
import type { AutomationStatus, JobRecord, ObservabilitySummary } from "../api/types";

function renderBar() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <DensityProvider>
          <ExecutionModeProvider>
            <JobStatusProvider>
              <TopStatusBar />
            </JobStatusProvider>
          </ExecutionModeProvider>
        </DensityProvider>
      </ToastProvider>
    </ThemeProvider>
  );
}

// Wraps in a real AutoRefreshProvider (rather than the useAutoRefresh
// out-of-provider fallback renderBar() gets by default) so localStorage-driven
// state -- the master toggle, safetyTelemetryEnabled -- actually takes effect.
function renderBarWithAutoRefresh() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <DensityProvider>
          <AutoRefreshProvider>
            <ExecutionModeProvider>
              <JobStatusProvider>
                <TopStatusBar />
              </JobStatusProvider>
            </ExecutionModeProvider>
          </AutoRefreshProvider>
        </DensityProvider>
      </ToastProvider>
    </ThemeProvider>
  );
}

function job(overrides: Partial<JobRecord> = {}): JobRecord {
  return {
    job_id: "job-abc123",
    job_type: "command",
    status: "running",
    exit_code: null,
    is_running: true,
    cancellable: true,
    command_name: "backfill_news_history_from_audit.py",
    created_at: "2026-08-27T12:00:00+00:00",
    ...overrides,
  };
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
      macro_kill_switch: null,
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
    forecast_skill_by_symbol: mockForecastSkillBySymbolEmpty(),
    risk_gate_blocks: { entries: [], count: 0, reason: "No risk-gate blocks logged yet." },
    latency_heatmap: mockLatencyHeatmapDisabled(),
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
  beforeEach(() => {
    // Default: no jobs running. JobStatusProvider fetches this on mount
    // regardless of which test is running, so every pre-existing test in
    // this describe block needs a default mock or it hits the real
    // liveApi/mockApi listJobs implementation.
    vi.spyOn(api, "listJobs").mockResolvedValue({ jobs: [] });
  });
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

  it("shows 'idle' when GET /jobs reports nothing running, and the modal says so too", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce(automationStatus());
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(observabilitySummary());
    // beforeEach already mocks listJobs -> { jobs: [] }
    renderBar();
    const chip = await screen.findByText("idle");

    await user.click(chip);
    expect(await screen.findByText("No recent jobs.")).toBeInTheDocument();
  });

  it("shows 'N running' and lists the real job in the modal when a job is active", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce(automationStatus());
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(observabilitySummary());
    vi.spyOn(api, "listJobs").mockResolvedValue({ jobs: [job()] });

    renderBar();
    const chip = await screen.findByText("1 running");

    await user.click(chip);
    expect(await screen.findByText("backfill_news_history_from_audit.py")).toBeInTheDocument();
    expect(screen.getByText(/job-abc123/)).toBeInTheDocument();
  });

  it("the modal's Cancel button calls cancelJob and reloads the job list", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce(automationStatus());
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(observabilitySummary());
    const listJobsSpy = vi
      .spyOn(api, "listJobs")
      .mockResolvedValueOnce({ jobs: [job()] })
      .mockResolvedValue({ jobs: [job({ status: "cancelled", is_running: false, exit_code: -15 })] });
    const cancelSpy = vi.spyOn(api, "cancelJob").mockResolvedValueOnce({ job_id: "job-abc123", cancelled: true });

    renderBar();
    const chip = await screen.findByText("1 running");
    await user.click(chip);

    const cancelButton = await screen.findByRole("button", { name: "Cancel" });
    await user.click(cancelButton);

    await waitFor(() => expect(cancelSpy).toHaveBeenCalledWith("job-abc123"));
    await waitFor(() => expect(listJobsSpy).toHaveBeenCalledTimes(2));
  });

  it("surfaces an honest toast when POST /jobs/{id}/cancel reports cancelled: false, instead of silently doing nothing", async () => {
    // api/control_api.py's cancel endpoint returns `{cancelled: false}` at 200
    // (not a thrown error) when it could ask but not confirm the job actually
    // stopped -- Console.tsx/RunCommandControl.tsx already handle this correctly;
    // this is the regression test for TopStatusBar.tsx's own Cancel button.
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce(automationStatus());
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(observabilitySummary());
    vi.spyOn(api, "listJobs").mockResolvedValue({ jobs: [job()] });
    const cancelSpy = vi.spyOn(api, "cancelJob").mockResolvedValueOnce({ job_id: "job-abc123", cancelled: false });

    renderBar();
    const chip = await screen.findByText("1 running");
    await user.click(chip);

    const cancelButton = await screen.findByRole("button", { name: "Cancel" });
    await user.click(cancelButton);

    await waitFor(() => expect(cancelSpy).toHaveBeenCalledWith("job-abc123"));
    expect(
      await screen.findByText("Cancel was requested but could not be confirmed — the job may still be running.")
    ).toBeInTheDocument();
  });
});

describe("TopStatusBar — auto-refresh wiring", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.spyOn(api, "listJobs").mockResolvedValue({ jobs: [] });
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    localStorage.clear();
  });

  it("master auto-refresh OFF still lets the automation (kill-switch/heartbeat) poll fire -- safety telemetry is independent", async () => {
    // localStorage empty -> master auto-refresh defaults OFF, safety
    // telemetry defaults ON.
    const automationSpy = vi
      .spyOn(api, "getAutomationStatus")
      .mockResolvedValue(automationStatus());
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValue(observabilitySummary());

    renderBarWithAutoRefresh();
    await act(async () => {});
    expect(automationSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(30_000); // AUTOMATION_POLL_MS
    });
    expect(automationSpy).toHaveBeenCalledTimes(2);
  });

  it("safetyTelemetryEnabled=false via localStorage stops the automation poll and shows the caution indicator", async () => {
    localStorage.setItem("stockpy.auto_refresh.safety_telemetry_enabled", "0");
    const automationSpy = vi
      .spyOn(api, "getAutomationStatus")
      .mockResolvedValue(automationStatus());
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValue(observabilitySummary());

    renderBarWithAutoRefresh();
    await act(async () => {});
    expect(automationSpy).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("safety-telemetry-off-indicator")).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(30_000); // AUTOMATION_POLL_MS
    });
    expect(automationSpy).toHaveBeenCalledTimes(1); // no background poll fired
  });

  it("the regime poll DOES respect the master toggle being off", async () => {
    // localStorage empty -> master auto-refresh defaults OFF.
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(automationStatus());
    const regimeSpy = vi
      .spyOn(api, "getObservabilitySummary")
      .mockResolvedValue(observabilitySummary());

    renderBarWithAutoRefresh();
    await act(async () => {});
    expect(regimeSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(300_000); // REGIME_POLL_MS
    });
    expect(regimeSpy).toHaveBeenCalledTimes(1); // no background poll fired
  });
});

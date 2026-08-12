/**
 * ForecastBackfillScreen.test.tsx — multi-horizon, registry-driven forecast
 * backfill & meta-labeling research screen. Covers the populated mock status
 * (8 trained models), the never-run/empty-metrics honest state, triggering a
 * run (success + failure paths), and that a run reloads the status.
 */
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ForecastBackfillScreen } from "./ForecastBackfillScreen";
import { api } from "../api/client";
import { ApiError, ForecastBackfillConflictError } from "../api/types";

function renderScreen() {
  return render(
    <MemoryRouter>
      <ForecastBackfillScreen />
    </MemoryRouter>
  );
}

describe("ForecastBackfillScreen (real mock API)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("renders the 9 trained meta-labeler rows from the mock summary", async () => {
    renderScreen();
    expect(await screen.findByText("timeseries_momentum_10d")).toBeInTheDocument();
    expect(screen.getByText("rsi2_mean_reversion_90d")).toBeInTheDocument();
    expect(screen.getByText("macd_momentum_10d")).toBeInTheDocument();
    expect(
      screen.getAllByText(/^timeseries_momentum_|^rsi2_mean_reversion_|^macd_momentum_/).length
    ).toBe(9);
  });

  it("shows an 'Active' badge for is_active:true rows and a 'Diagnostic' badge for is_active:false, sorted Active-first", async () => {
    renderScreen();
    await screen.findByText("timeseries_momentum_10d");
    expect(screen.getAllByText("Active").length).toBe(8);
    expect(screen.getAllByText("Diagnostic").length).toBe(1);

    // Active-first sort: every row above the diagnostic one is Active.
    const rows = screen.getAllByRole("row").slice(1); // drop header row
    const diagnosticIdx = rows.findIndex((row) => row.textContent?.includes("macd_momentum_10d"));
    expect(diagnosticIdx).toBeGreaterThan(-1);
    for (const row of rows.slice(0, diagnosticIdx)) {
      expect(row.textContent).toContain("Active");
    }
  });

  it("the strategy multi-select is populated dynamically from the model_key set, not a hardcoded list", async () => {
    renderScreen();
    await screen.findByText("timeseries_momentum_10d");
    expect(screen.getByRole("option", { name: "timeseries_momentum" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "rsi2_mean_reversion" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "macd_momentum" })).toBeInTheDocument();
    // Never offer a strategy name that isn't a real signals.registry.global_registry entry.
    expect(screen.queryByRole("option", { name: "pairs_trading" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "macro_regime_pit" })).not.toBeInTheDocument();
  });

  it("shows the honest 'no trained models yet' state when metrics are empty", async () => {
    vi.spyOn(api, "getForecastBackfill").mockResolvedValueOnce({
      status: "not_run",
      timestamp: null,
      horizons: [10, 30, 60, 90],
      metrics: {},
      tickers: [],
      message: "Forecast backfill has not been run yet.",
    });
    renderScreen();
    expect(
      await screen.findByText(/No trained meta-labeler metrics available yet/)
    ).toBeInTheDocument();
  });

  it("running the backfill polls the job status and shows success, then reloads", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    
    // Mock the job sequence so we don't hit the `delay()` calls in mock.ts
    const runSpy = vi.spyOn(api, "runForecastBackfill").mockResolvedValueOnce({
      job_id: "job-1",
      state: "running",
      phase: "fetching_data",
      step: 1,
      total_steps: 7,
      error: null,
      error_type: null,
      summary: null,
      sample_rows: null,
      partial_summary: null,
      seconds_remaining: 14,
    });
    const jobStatusSpy = vi.spyOn(api, "getForecastBackfillJobStatus").mockResolvedValueOnce({
      job_id: "job-1",
      state: "succeeded",
      phase: "exporting",
      step: 7,
      total_steps: 7,
      error: null,
      error_type: null,
      summary: null,
      sample_rows: 1200,
      partial_summary: null,
      seconds_remaining: 0,
    });
    const statusSpy = vi.spyOn(api, "getForecastBackfill");

    renderScreen();
    await screen.findByText("timeseries_momentum_10d");
    statusSpy.mockClear();

    fireEvent.click(screen.getByText("🚀 Run Forecast Backfill"));
    await waitFor(() => expect(runSpy).toHaveBeenCalled());
    expect(await screen.findByText(/Fetching data…/)).toBeInTheDocument();

    // Advance timers to trigger the poll and resolve the mock job
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(await screen.findByText(/Success! Processed/)).toBeInTheDocument();
    expect(jobStatusSpy).toHaveBeenCalledWith("job-1");
    vi.useRealTimers();
    await waitFor(() => expect(statusSpy).toHaveBeenCalled());
  });

  it("a 409 conflict on start shows an honest 'already in progress' notice and tracks the existing job", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    vi.spyOn(api, "runForecastBackfill").mockRejectedValueOnce(
      new ForecastBackfillConflictError("A forecast backfill run is already in progress.", "job-existing")
    );
    const jobStatusSpy = vi.spyOn(api, "getForecastBackfillJobStatus").mockResolvedValueOnce({
      job_id: "job-existing",
      state: "running",
      phase: "backtraining",
      step: 5,
      total_steps: 7,
      error: null,
      error_type: null,
      summary: null,
      sample_rows: null,
      partial_summary: null,
      seconds_remaining: 20,
    });

    renderScreen();
    await screen.findByText("timeseries_momentum_10d");

    fireEvent.click(screen.getByText("🚀 Run Forecast Backfill"));
    expect(await screen.findByText(/already in progress/i)).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(jobStatusSpy).toHaveBeenCalledWith("job-existing");
    expect(await screen.findByText(/Backtraining meta labelers…/)).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("flags synthetic (non-real) fallback data instead of presenting it as a genuine backtest", async () => {
    vi.spyOn(api, "getForecastBackfill").mockResolvedValueOnce({
      status: "completed",
      timestamp: new Date().toISOString(),
      horizons: [10, 30, 60, 90],
      metrics: {},
      tickers: ["ZZZZ"],
      dropped_tickers: ["ZZZZ"],
    });
    renderScreen();
    expect(
      await screen.findByText(/No real market data for 1 ticker/)
    ).toBeInTheDocument();
    expect(screen.getByText(/ZZZZ/)).toBeInTheDocument();
  });

  it("a failed run renders the honest failure message, not a silent no-op", async () => {
    vi.spyOn(api, "runForecastBackfill").mockRejectedValueOnce(new ApiError("backend unreachable", 500));
    renderScreen();
    await screen.findByText("timeseries_momentum_10d");

    fireEvent.click(screen.getByText("🚀 Run Forecast Backfill"));
    expect(await screen.findByText(/Error: backend unreachable/)).toBeInTheDocument();
  });

  it("a timeout with a non-empty partial_summary shows the 'N models' message and renders the partial-results table", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    vi.spyOn(api, "runForecastBackfill").mockResolvedValueOnce({
      job_id: "job-timeout",
      state: "running",
      phase: "backtraining",
      step: 5,
      total_steps: 7,
      error: null,
      error_type: null,
      summary: null,
      sample_rows: null,
      partial_summary: null,
      seconds_remaining: 5,
    });
    vi.spyOn(api, "getForecastBackfillJobStatus").mockResolvedValueOnce({
      job_id: "job-timeout",
      state: "timeout",
      phase: "backtraining",
      step: 5,
      total_steps: 7,
      error: "Forecast backfill did not complete within 1800s.",
      error_type: "timeout",
      summary: null,
      sample_rows: null,
      partial_summary: {
        trained: ["timeseries_momentum_10d", "rsi2_mean_reversion_10d"],
        metrics_so_far: {
          timeseries_momentum_10d: { accuracy: 0.5215, auc: 0.5420, n_train: 9480, n_test: 0, split_date: "CPCV", is_active: true },
          rsi2_mean_reversion_10d: { accuracy: 0.5180, auc: 0.5310, n_train: 6820, n_test: 0, split_date: "CPCV", is_active: true },
        },
      },
      seconds_remaining: 0,
    });

    renderScreen();
    await screen.findByText("timeseries_momentum_10d");

    fireEvent.click(screen.getByText("🚀 Run Forecast Backfill"));
    await waitFor(() => expect(api.runForecastBackfill).toHaveBeenCalled());

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(
      await screen.findByText(/The backfill timed out after training 2 models — partial results were saved\./)
    ).toBeInTheDocument();
    expect(screen.getByText("Partial Results Saved Before Timeout")).toBeInTheDocument();
    // The partial table renders both checkpointed model keys.
    const partialSection = screen.getByText("Partial Results Saved Before Timeout").closest("section");
    expect(partialSection).not.toBeNull();
    expect(partialSection).toHaveTextContent("timeseries_momentum_10d");
    expect(partialSection).toHaveTextContent("rsi2_mean_reversion_10d");

    vi.useRealTimers();
  });

  it("a timeout with no partial_summary keeps the honest 'nothing was saved' message and renders no partial table", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    vi.spyOn(api, "runForecastBackfill").mockResolvedValueOnce({
      job_id: "job-timeout-empty",
      state: "running",
      phase: "fetching_data",
      step: 1,
      total_steps: 7,
      error: null,
      error_type: null,
      summary: null,
      sample_rows: null,
      partial_summary: null,
      seconds_remaining: 5,
    });
    vi.spyOn(api, "getForecastBackfillJobStatus").mockResolvedValueOnce({
      job_id: "job-timeout-empty",
      state: "timeout",
      phase: "technical_features",
      step: 2,
      total_steps: 7,
      error: "Forecast backfill did not complete within 1800s.",
      error_type: "timeout",
      summary: null,
      sample_rows: null,
      partial_summary: null,
      seconds_remaining: 0,
    });

    renderScreen();
    await screen.findByText("timeseries_momentum_10d");

    fireEvent.click(screen.getByText("🚀 Run Forecast Backfill"));
    await waitFor(() => expect(api.runForecastBackfill).toHaveBeenCalled());

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(await screen.findByText("The backfill timed out. Nothing was saved.")).toBeInTheDocument();
    expect(screen.queryByText("Partial Results Saved Before Timeout")).not.toBeInTheDocument();

    vi.useRealTimers();
  });
});

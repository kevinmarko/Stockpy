/**
 * ForecastBackfillScreen.test.tsx — multi-horizon, registry-driven forecast
 * backfill & meta-labeling research screen. Covers the populated mock status
 * (8 trained models), the never-run/empty-metrics honest state, triggering a
 * run (success + failure paths), and that a run reloads the status.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ForecastBackfillScreen } from "./ForecastBackfillScreen";
import { api } from "../api/client";

function renderScreen() {
  return render(
    <MemoryRouter>
      <ForecastBackfillScreen />
    </MemoryRouter>
  );
}

describe("ForecastBackfillScreen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

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

  it("running the backfill calls the API and shows a success message, then reloads status", async () => {
    const runSpy = vi.spyOn(api, "runForecastBackfill");
    const statusSpy = vi.spyOn(api, "getForecastBackfill");
    renderScreen();
    await screen.findByText("timeseries_momentum_10d");
    statusSpy.mockClear();

    fireEvent.click(screen.getByText("🚀 Run Forecast Backfill"));
    await waitFor(() => expect(runSpy).toHaveBeenCalled());
    expect(await screen.findByText(/Success! Processed/)).toBeInTheDocument();
    await waitFor(() => expect(statusSpy).toHaveBeenCalled());
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
    vi.spyOn(api, "runForecastBackfill").mockRejectedValueOnce(new Error("backend unreachable"));
    renderScreen();
    await screen.findByText("timeseries_momentum_10d");

    fireEvent.click(screen.getByText("🚀 Run Forecast Backfill"));
    expect(await screen.findByText(/Backfill failed: backend unreachable/)).toBeInTheDocument();
  });
});

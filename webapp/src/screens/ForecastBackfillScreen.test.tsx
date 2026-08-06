/**
 * ForecastBackfillScreen.test.tsx — multi-horizon TSMOM/CSMOM forecast
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

  it("renders the 8 trained meta-labeler rows from the mock summary", async () => {
    renderScreen();
    expect(await screen.findByText("TSMOM_10d")).toBeInTheDocument();
    expect(screen.getByText("CSMOM_90d")).toBeInTheDocument();
    expect(screen.getAllByText(/^TSMOM_|^CSMOM_/).length).toBe(8);
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
    await screen.findByText("TSMOM_10d");
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
    await screen.findByText("TSMOM_10d");

    fireEvent.click(screen.getByText("🚀 Run Forecast Backfill"));
    expect(await screen.findByText(/Backfill failed: backend unreachable/)).toBeInTheDocument();
  });
});

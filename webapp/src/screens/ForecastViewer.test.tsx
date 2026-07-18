/**
 * ForecastViewer.test.tsx — multi-horizon forecast + MC band + price/forecast
 * candle chart. Covers the happy path, a null horizon rendering "—", the 404
 * cold-start honest state, the chart section appearing once both fetches
 * resolve, the range toggle re-fetching bars at a new lookback, and the
 * bars-empty fallback not blocking the forecast tiles.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ForecastViewer } from "./ForecastViewer";
import { api } from "../api/client";
import { ApiError } from "../api/types";

function renderScreen() {
  return render(
    <MemoryRouter>
      <ForecastViewer />
    </MemoryRouter>
  );
}

describe("ForecastViewer screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the horizon tiles for the default symbol", async () => {
    renderScreen();
    expect(await screen.findByText("Model detail")).toBeInTheDocument();
    expect(screen.getByText("ARIMA")).toBeInTheDocument();
  });

  it("a null horizon (Forecast_90) renders '—', never a fabricated level", async () => {
    renderScreen();
    await screen.findByText("Model detail");
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("a 404 (no bars) renders the honest cold-start state", async () => {
    vi.spyOn(api, "getForecastResult").mockRejectedValueOnce(
      new ApiError("No bar data available for ZZZZ", 404)
    );
    renderScreen();
    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
  });

  it("renders the price & forecast chart section once both fetches resolve", async () => {
    renderScreen();
    expect(await screen.findByText("Price & forecast")).toBeInTheDocument();
    // Model detail (the second card) confirms the forecast fetch resolved too.
    expect(await screen.findByText("Model detail")).toBeInTheDocument();
  });

  it("changing the range toggle re-fetches bars with the new lookback", async () => {
    const spy = vi.spyOn(api, "getDataBars");
    renderScreen();
    await screen.findByText("Price & forecast");
    // Default range is 3M -> 63 days.
    await waitFor(() => expect(spy).toHaveBeenCalledWith("AAPL", 63));

    fireEvent.click(screen.getByText("1M"));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("AAPL", 21));
  });

  it("bars-empty fallback: forecast tiles still render when the store has no history for this symbol", async () => {
    vi.spyOn(api, "getDataBars").mockResolvedValueOnce([]);
    renderScreen();
    expect(await screen.findByText("Model detail")).toBeInTheDocument();
    expect(screen.getByText("Price & forecast")).toBeInTheDocument();
    expect(
      await screen.findByText(/No price history in the store for this symbol/)
    ).toBeInTheDocument();
  });
});

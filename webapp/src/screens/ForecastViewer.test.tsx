/**
 * ForecastViewer.test.tsx — multi-horizon forecast + MC band. Covers the happy
 * path, a null horizon rendering "—", and the 404 cold-start honest state.
 */
import { render, screen } from "@testing-library/react";
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
});

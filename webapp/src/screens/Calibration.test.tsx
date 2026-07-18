/**
 * Calibration.test.tsx — the "did our actual calls work?" screen.
 *
 * Covers the happy render (calibration diagram, rec-tracking tiles, MFE/MAE
 * scatter, decision journal), the honesty branches the mock fixture exercises
 * (an under-min calibration bin → "insufficient data"; a null model return →
 * "—"; an unlinked decision → "no trade match"), the cold-start empty states
 * (no closed trades / no logged signals), and the decision write flow
 * (click a signal → Acted → the server result renders, never assumed).
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Calibration } from "./Calibration";
import { api } from "../api/client";
import type { CalibrationSummary, EdgeByStrategy } from "../api/types";

function renderScreen() {
  return render(
    <MemoryRouter>
      <Calibration />
    </MemoryRouter>
  );
}

const EMPTY_SUMMARY: CalibrationSummary = {
  calibration: {
    bins: [],
    total: 0,
    overall_win_rate: null,
    calibration_error: null,
    n_scored_bins: 0,
    n_bins: 10,
    min_trades_per_bin: 5,
    reason: "No conviction-annotated closed trades yet.",
  },
  recommendation_tracking: {
    horizon_days: 30,
    model_return: null,
    operator_return: null,
    delta: null,
    n_signals: 0,
    n_acted: 0,
    n_completed: 0,
    n_with_exit: 0,
    rows: [],
    reason: "No BUY signals in the decision log yet.",
  },
  mfe_mae: { points: [], reason: "No MFE/MAE excursion data yet." },
  recent_decisions: { decisions: [], reason: "No decisions logged yet." },
};

describe("Calibration screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the four section headings and calibration summary tiles", async () => {
    renderScreen();
    expect(await screen.findByText("Conviction calibration")).toBeInTheDocument();
    expect(screen.getByText("Recommendation tracking")).toBeInTheDocument();
    expect(screen.getByText("Trade quality — MFE / MAE")).toBeInTheDocument();
    expect(screen.getByText("Decision journal")).toBeInTheDocument();
    // Calibration summary tile from the fixture (41 trades w/ conviction).
    expect(screen.getByText("Trades w/ conviction")).toBeInTheDocument();
    expect(screen.getByText("41")).toBeInTheDocument();
  });

  it("shows the honest 'insufficient data' note for an under-min calibration bin", async () => {
    renderScreen();
    // The fixture has one bin with count 2 (< min_trades_per_bin=5) → win_rate null.
    expect(
      await screen.findByText(/had fewer than 5 trades — shown as insufficient data/)
    ).toBeInTheDocument();
  });

  it("renders a null model/actual return as '—', never a fabricated 0.0", async () => {
    renderScreen();
    // The NVDA fixture row is not completed (model_return null, actual_return null).
    const nvdaCell = await screen.findByText("NVDA");
    const row = nvdaCell.closest("tr")!;
    expect(within(row).getByText(/pending/)).toBeInTheDocument();
    // Its model + actual return cells are the em-dash placeholder.
    expect(within(row).getAllByText("—").length).toBeGreaterThan(0);
  });

  it("cold start: no closed trades and no logged signals render honest empty states", async () => {
    vi.spyOn(api, "getCalibrationSummary").mockResolvedValueOnce(EMPTY_SUMMARY);
    renderScreen();
    expect(await screen.findByText("No conviction data yet")).toBeInTheDocument();
    expect(screen.getByText("No BUY signals logged yet")).toBeInTheDocument();
    expect(screen.getByText("No excursion data yet")).toBeInTheDocument();
    expect(screen.getByText("No current signals to journal")).toBeInTheDocument();
  });

  it("lazy-loads edge-by-strategy only after the Compute button is clicked", async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(api, "getEdgeByStrategy");
    renderScreen();
    await screen.findByText("Edge ratio by strategy");
    // Not fetched on initial render.
    expect(spy).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: /Compute edge ratio by strategy/ })
    );
    // Now the heavier recompute runs and its rows render.
    expect(await screen.findByText("trend-following")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("edge-by-strategy honest empty when there are no closed trades", async () => {
    const user = userEvent.setup();
    const empty: EdgeByStrategy = { rows: [], reason: "No closed trades yet." };
    vi.spyOn(api, "getEdgeByStrategy").mockResolvedValueOnce(empty);
    renderScreen();
    await screen.findByText("Edge ratio by strategy");
    await user.click(
      screen.getByRole("button", { name: /Compute edge ratio by strategy/ })
    );
    expect(await screen.findByText("No closed trades to score yet")).toBeInTheDocument();
  });

  it("decision write flow: click a signal → Acted → the server result renders (unlinked)", async () => {
    const user = userEvent.setup();
    renderScreen();
    // XOM is a current signal in the fixture; acting on it has no matching trade
    // (only AAPL links in the mock) → "no trade match within 24h".
    await screen.findByText("Decision journal");
    const xomRow = screen.getByText("XOM").closest(".card") as HTMLElement;
    await user.click(within(xomRow).getByRole("button", { name: "Log decision" }));
    // Modal opened for XOM.
    expect(await screen.findByText("Log decision — XOM")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Acted/ }));
    const result = await screen.findByTestId("decision-result");
    expect(result).toHaveTextContent(/Logged:/);
    expect(result).toHaveTextContent(/no trade match within 24h/);
  });

  it("decision write flow: an acted AAPL signal renders the linked trade id", async () => {
    const user = userEvent.setup();
    renderScreen();
    await screen.findByText("Decision journal");
    // AAPL appears in several sections; scope to the decision-journal card list.
    const aaplButtons = screen.getAllByRole("button", { name: "Log decision" });
    // The first journal row is AAPL (points order: AAPL, MSFT, XOM).
    await user.click(aaplButtons[0]);
    expect(await screen.findByText("Log decision — AAPL")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Acted/ }));
    const result = await screen.findByTestId("decision-result");
    expect(result).toHaveTextContent(/linked to trade #42/);
  });

  it("Modified is disabled until a note is entered", async () => {
    const user = userEvent.setup();
    renderScreen();
    await screen.findByText("Decision journal");
    const buttons = screen.getAllByRole("button", { name: "Log decision" });
    await user.click(buttons[0]);
    await screen.findByText(/Log decision — /);
    const modifiedBtn = screen.getByRole("button", { name: /Modified/ });
    expect(modifiedBtn).toBeDisabled();
    await user.type(screen.getByPlaceholderText(/Halved size/), "trimmed to half");
    expect(modifiedBtn).not.toBeDisabled();
  });
});

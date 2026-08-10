/**
 * StrategyInsights.test.tsx
 *
 * Covers the happy render (edge-by-strategy charts, price/decision overlay,
 * Pilots table with expandable holdings, simulate panel) and — the actual
 * regression this screen exists to prevent — that two different mock
 * `simulatePilotAllocation` responses for two different pilots produce
 * VISIBLY DIFFERENT rendered deltas. PR #670 (unmerged) shipped a "What-If
 * Simulation" panel with a hardcoded delta identical for every strategy;
 * this test would have caught that.
 *
 * `api.getHoldings` / `api.simulatePilotAllocation` are being added to
 * client.ts/mock.ts by a parallel workstream and don't exist on the mock
 * object yet, so they're assigned directly here (not via `vi.spyOn`, which
 * requires the property to already exist) rather than through the usual
 * spyOn convention used for every other endpoint in this file.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StrategyInsights } from "./StrategyInsights";
import { api } from "../api/client";
import type { Holding } from "../api/types";

function renderScreen() {
  return render(
    <MemoryRouter>
      <StrategyInsights />
    </MemoryRouter>
  );
}

const MOCK_HOLDINGS: Holding[] = [
  {
    symbol: "AAPL",
    name: "Apple Inc.",
    sector: "Technology",
    weight: 0.32,
    score: 7.4,
    price: 214.9,
    action: "BUY",
    buy_range: "$210 - $216",
    sell_range: "$225 - $230",
    conviction: 0.7,
    meta_label_composite: 0.6,
  },
];

function mockSimulationFor(pilotId: string, sharpeDelta: number) {
  return {
    pilot_id: pilotId,
    current: { sharpe_ratio: 1.1, max_drawdown: 0.18 },
    projected: { sharpe_ratio: 1.1 + sharpeDelta, max_drawdown: 0.18 - sharpeDelta * 0.02 },
    heat_pct_current: 0.42,
    heat_pct_projected: null as null,
    coverage: { symbols_covered: 8, symbols_total: 10 },
    reason: null,
  };
}

describe("Strategy Insights screen", () => {
  beforeEach(() => {
    (api as any).getHoldings = vi.fn().mockResolvedValue(MOCK_HOLDINGS);
    (api as any).simulatePilotAllocation = vi
      .fn()
      .mockImplementation((pilotId: string) => Promise.resolve(mockSimulationFor(pilotId, 0.15)));
  });

  afterEach(() => {
    vi.restoreAllMocks();
    delete (api as any).getHoldings;
    delete (api as any).simulatePilotAllocation;
  });

  it("renders the edge charts, price chart, strategies table, and simulate panel with no console errors", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    renderScreen();

    expect(await screen.findByText("Strategy Insights")).toBeInTheDocument();

    // Edge per strategy — two stacked single-axis bar charts (never one
    // dual-axis chart).
    expect(await screen.findByTestId("strategy-insights-edge-chart")).toBeInTheDocument();
    expect(screen.getByText("Mean edge ratio by strategy")).toBeInTheDocument();
    expect(screen.getByText("Trade count by strategy")).toBeInTheDocument();

    // Price history + decision overlay, real functional symbol select.
    const select = await screen.findByTestId("strategy-insights-symbol-select");
    expect(select).toBeInTheDocument();
    expect(await screen.findByTestId("strategy-insights-price-chart")).toBeInTheDocument();

    // Strategies table.
    expect(await screen.findByTestId("strategy-insights-table")).toBeInTheDocument();
    expect(await screen.findByTestId("strategy-row-trend-following")).toBeInTheDocument();

    // Simulate panel — no pilot picked yet.
    expect(screen.getByTestId("simulate-panel")).toBeInTheDocument();
    expect(
      screen.getByText(/Pick a Pilot from the table above/)
    ).toBeInTheDocument();

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("symbol select is real and functional — switching symbols re-fetches bars/decisions", async () => {
    const user = userEvent.setup();
    const barsSpy = vi.spyOn(api, "getDataBars");
    renderScreen();

    const select = (await screen.findByTestId("strategy-insights-symbol-select")) as HTMLSelectElement;
    await screen.findByTestId("strategy-insights-price-chart");
    expect(barsSpy).toHaveBeenCalledWith("AAPL", 252);

    await user.selectOptions(select, "MSFT");
    expect(await screen.findByTestId("strategy-insights-price-chart")).toBeInTheDocument();
    expect(barsSpy).toHaveBeenCalledWith("MSFT", 252);
  });

  it("expands a pilot row to show its holdings via api.getHoldings", async () => {
    const user = userEvent.setup();
    renderScreen();

    const row = await screen.findByTestId("strategy-row-trend-following");
    await user.click(within(row).getByTestId("strategy-holdings-toggle-trend-following"));

    const holdings = await screen.findByTestId("strategy-holdings-trend-following");
    expect(within(holdings).getByText("AAPL")).toBeInTheDocument();
    expect((api as any).getHoldings).toHaveBeenCalledWith("trend-following");
  });

  it("REGRESSION (PR #670): two different pilots' simulations render visibly different deltas, never a hardcoded constant", async () => {
    const user = userEvent.setup();
    (api as any).simulatePilotAllocation = vi.fn().mockImplementation((pilotId: string) => {
      if (pilotId === "trend-following") return Promise.resolve(mockSimulationFor(pilotId, 0.15));
      return Promise.resolve(mockSimulationFor(pilotId, -0.42));
    });
    renderScreen();

    // Simulate pilot A ("trend-following").
    const rowA = await screen.findByTestId("strategy-row-trend-following");
    await user.click(within(rowA).getByTestId("strategy-simulate-trend-following"));
    await user.click(screen.getByTestId("simulate-run"));
    const resultA = await screen.findByTestId("simulate-result");
    expect(resultA).toHaveTextContent("1.10 → 1.25");
    expect(resultA).toHaveTextContent("(+0.150)");
    // Capture the rendered text NOW -- `resultA` is a live DOM node that
    // React re-renders in place, so re-reading `.textContent` after the
    // second simulation below would silently reflect pilot B's numbers too.
    const textA = resultA.textContent;

    // Now simulate pilot B ("dip-buyer") with a DIFFERENT allocation result.
    const rowB = await screen.findByTestId("strategy-row-dip-buyer");
    await user.click(within(rowB).getByTestId("strategy-simulate-dip-buyer"));
    await user.click(screen.getByTestId("simulate-run"));
    const resultB = await screen.findByTestId("simulate-result");
    expect(resultB).toHaveTextContent("1.10 → 0.68");
    expect(resultB).toHaveTextContent("(-0.420)");

    // The two rendered deltas are genuinely different -- not the same
    // hardcoded number regardless of which pilot/amount was simulated.
    expect(textA).not.toEqual(resultB.textContent);

    // heat_pct_projected is ALWAYS rendered as the honest unavailable note,
    // never a fabricated number, regardless of pilot.
    expect(resultB).toHaveTextContent("Not available for hypothetical positions");
  });

  it("shows a real loading state (disabled, pending) and a real error state for the simulate button", async () => {
    const user = userEvent.setup();
    let resolveFn: (v: unknown) => void = () => {};
    (api as any).simulatePilotAllocation = vi.fn().mockImplementation(
      () => new Promise((res) => { resolveFn = res; })
    );
    renderScreen();

    const row = await screen.findByTestId("strategy-row-trend-following");
    await user.click(within(row).getByTestId("strategy-simulate-trend-following"));
    const runBtn = screen.getByTestId("simulate-run");
    await user.click(runBtn);
    expect(runBtn).toBeDisabled();

    resolveFn(mockSimulationFor("trend-following", 0.1));
    expect(await screen.findByTestId("simulate-result")).toBeInTheDocument();
  });

  it("simulate error state renders a message, not a silently blank panel", async () => {
    const user = userEvent.setup();
    (api as any).simulatePilotAllocation = vi.fn().mockRejectedValue(new Error("simulate failed"));
    renderScreen();

    const row = await screen.findByTestId("strategy-row-trend-following");
    await user.click(within(row).getByTestId("strategy-simulate-trend-following"));
    await user.click(screen.getByTestId("simulate-run"));

    expect(await screen.findByText("simulate failed")).toBeInTheDocument();
  });

  it("surfaces `reason` verbatim when a simulation field is null", async () => {
    const user = userEvent.setup();
    (api as any).simulatePilotAllocation = vi.fn().mockResolvedValue({
      pilot_id: "trend-following",
      current: { sharpe_ratio: null, max_drawdown: 0.2 },
      projected: { sharpe_ratio: null, max_drawdown: 0.19 },
      heat_pct_current: 0.4,
      heat_pct_projected: null,
      coverage: { symbols_covered: 3, symbols_total: 10 },
      reason: "Insufficient validated Sharpe history for this Pilot.",
    });
    renderScreen();

    const row = await screen.findByTestId("strategy-row-trend-following");
    await user.click(within(row).getByTestId("strategy-simulate-trend-following"));
    await user.click(screen.getByTestId("simulate-run"));

    expect(
      await screen.findByText("Insufficient validated Sharpe history for this Pilot.")
    ).toBeInTheDocument();
  });
});

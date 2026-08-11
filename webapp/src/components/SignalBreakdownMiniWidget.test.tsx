/**
 * SignalBreakdownMiniWidget.test.tsx
 *
 * Covers the happy-path render, loading state, error state, and the real
 * empty/cold-start state (never a fabricated 0/placeholder in place of
 * missing signal data -- CONSTRAINT #4). `api` is already the mock
 * (VITE_USE_MOCK default-true) -- we never vi.mock the module; we spy on
 * individual api methods only for the fixtures each test needs, mirroring
 * SentimentMiniChart.test.tsx / StrategyInsights.test.tsx's convention.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SignalBreakdownMiniWidget } from "./SignalBreakdownMiniWidget";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import type { Portfolio, PortfolioPositionView, SignalBreakdown } from "../api/types";

const EMPTY_PORTFOLIO: Portfolio = {
  total_equity: 0,
  buying_power: 0,
  total_unrealized_pl: 0,
  total_dividends: 0,
  position_count: 0,
  positions: [],
  fetched_at: null,
  source: "db",
};

function makePosition(symbol: string): PortfolioPositionView {
  return {
    symbol,
    qty: 1,
    avg_cost: 100,
    current_price: 100,
    market_value: 100,
    unrealized_pl: 0,
    unrealized_pl_pct: 0,
  };
}

function makeBreakdown(overrides: Partial<SignalBreakdown> = {}): SignalBreakdown {
  return {
    symbol: "AAPL",
    action: "BUY",
    conviction: 0.62,
    final_score: 5,
    modules: [
      { name: "cross_sectional_momentum", score: 0.8, weight: 2, contribution: 1.6 },
      { name: "rsi2_mean_reversion", score: -0.3, weight: 1, contribution: -0.3 },
    ],
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SignalBreakdownMiniWidget", () => {
  it("renders a real loading state before data arrives", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValue(EMPTY_PORTFOLIO);
    let resolveFn: (v: SignalBreakdown) => void = () => {};
    vi.spyOn(api, "getSignalBreakdown").mockImplementation(
      () => new Promise((res) => { resolveFn = res; })
    );

    const { container } = render(<SignalBreakdownMiniWidget />);

    expect(container.querySelector(".skeleton")).not.toBeNull();
    expect(screen.queryByTestId("signalBreakdown-widget")).not.toBeInTheDocument();

    resolveFn(makeBreakdown());
    expect(await screen.findByTestId("signalBreakdown-widget")).toBeInTheDocument();
  });

  it("renders the header and module bar chart on the happy path, using held positions as the symbol list", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValue({
      ...EMPTY_PORTFOLIO,
      positions: [makePosition("NVDA")],
    });
    const breakdownSpy = vi
      .spyOn(api, "getSignalBreakdown")
      .mockResolvedValue(makeBreakdown({ symbol: "NVDA" }));

    render(<SignalBreakdownMiniWidget />);

    const select = (await screen.findByTestId("signal-breakdown-widget-symbol-select")) as HTMLSelectElement;
    expect(select.value).toBe("NVDA");
    expect(await screen.findByTestId("signalBreakdown-widget")).toBeInTheDocument();

    expect(screen.getByText("BUY")).toBeInTheDocument();
    expect(screen.getByText("0.62")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    await waitFor(() => expect(breakdownSpy).toHaveBeenCalledWith("NVDA"));
  });

  it("falls back to the fixed fallback symbols when there are no held positions", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValue(EMPTY_PORTFOLIO);
    vi.spyOn(api, "getSignalBreakdown").mockResolvedValue(makeBreakdown());

    render(<SignalBreakdownMiniWidget />);

    const select = (await screen.findByTestId("signal-breakdown-widget-symbol-select")) as HTMLSelectElement;
    expect(select.value).toBe("AAPL");
    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("MSFT")).toBeInTheDocument();
    expect(screen.getByText("SPY")).toBeInTheDocument();
  });

  it("shows a real error state with Retry on a hard error", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValue(EMPTY_PORTFOLIO);
    const spy = vi
      .spyOn(api, "getSignalBreakdown")
      .mockRejectedValueOnce(new ApiError("boom", 500));

    render(<SignalBreakdownMiniWidget />);

    expect(await screen.findByText("Couldn't load")).toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();
    expect(screen.queryByTestId("signalBreakdown-widget")).not.toBeInTheDocument();

    spy.mockResolvedValueOnce(makeBreakdown());
    screen.getByRole("button", { name: "Retry" }).click();
    expect(await screen.findByTestId("signalBreakdown-widget")).toBeInTheDocument();
  });

  it("shows the real empty/cold-start state (never a fabricated 0) when modules is empty", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValue(EMPTY_PORTFOLIO);
    vi.spyOn(api, "getSignalBreakdown").mockResolvedValue(
      makeBreakdown({ action: null, conviction: null, final_score: null, modules: [] })
    );

    render(<SignalBreakdownMiniWidget />);

    expect(await screen.findByText("No signal data yet")).toBeInTheDocument();
    expect(
      screen.getByText("No scored signal modules for AAPL yet -- run the pipeline, then reload.")
    ).toBeInTheDocument();
    expect(screen.queryByTestId("signalBreakdown-widget")).not.toBeInTheDocument();
  });

  it("treats all-null module contributions (no real scored modules) as the empty state, not a blank chart", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValue(EMPTY_PORTFOLIO);
    vi.spyOn(api, "getSignalBreakdown").mockResolvedValue(
      makeBreakdown({
        modules: [
          { name: "cross_sectional_momentum", score: null, weight: 2, contribution: null },
          { name: "rsi2_mean_reversion", score: null, weight: 1, contribution: null },
        ],
      })
    );

    render(<SignalBreakdownMiniWidget />);

    expect(await screen.findByText("No signal data yet")).toBeInTheDocument();
    expect(screen.queryByTestId("signalBreakdown-widget")).not.toBeInTheDocument();
  });
});

/**
 * SentimentMiniChart.test.tsx
 *
 * Covers the happy-path render, loading state, error state, and the real
 * empty/cold-start state (never a fabricated 0/placeholder in place of
 * missing sentiment history -- CONSTRAINT #4). `api` is already the mock
 * (VITE_USE_MOCK default-true) -- we never vi.mock the module; we spy on
 * individual api methods only for the fixtures each test needs, mirroring
 * StrategyInsights.test.tsx / ActivityFeed.test.tsx's convention.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SentimentMiniChart } from "./SentimentMiniChart";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import type { Portfolio, PortfolioPositionView, SentimentHistory } from "../api/types";

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

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SentimentMiniChart", () => {
  it("renders a real loading state before data arrives", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValue(EMPTY_PORTFOLIO);
    let resolveFn: (v: SentimentHistory) => void = () => {};
    vi.spyOn(api, "getSentimentHistory").mockImplementation(
      () => new Promise((res) => { resolveFn = res; })
    );

    const { container } = render(<SentimentMiniChart />);

    expect(container.querySelector(".skeleton")).not.toBeNull();
    expect(screen.queryByTestId("sentimentMini-widget")).not.toBeInTheDocument();

    resolveFn({ symbol: "AAPL", points: [{ date: "2026-08-01", score: 0.2 }], reason: null });
    expect(await screen.findByTestId("sentimentMini-widget")).toBeInTheDocument();
  });

  it("renders the sentiment line chart on the happy path, using held positions as the symbol list", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValue({
      ...EMPTY_PORTFOLIO,
      positions: [makePosition("NVDA")],
    });
    const historySpy = vi.spyOn(api, "getSentimentHistory").mockResolvedValue({
      symbol: "NVDA",
      points: [
        { date: "2026-07-20", score: 0.1 },
        { date: "2026-07-21", score: null },
        { date: "2026-07-22", score: -0.3 },
      ],
      reason: null,
    });

    render(<SentimentMiniChart />);

    const select = (await screen.findByTestId("sentiment-mini-symbol-select")) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("NVDA"));
    await waitFor(() => expect(historySpy).toHaveBeenCalledWith("NVDA", 180));
    expect(await screen.findByTestId("sentimentMini-widget")).toBeInTheDocument();
    expect(screen.getByText("Sentiment score over time — NVDA")).toBeInTheDocument();
  });

  it("falls back to the fixed fallback symbols when there are no held positions", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValue(EMPTY_PORTFOLIO);
    vi.spyOn(api, "getSentimentHistory").mockResolvedValue({
      symbol: "AAPL",
      points: [{ date: "2026-08-01", score: 0.4 }],
      reason: null,
    });

    render(<SentimentMiniChart />);

    const select = (await screen.findByTestId("sentiment-mini-symbol-select")) as HTMLSelectElement;
    expect(select.value).toBe("AAPL");
    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("MSFT")).toBeInTheDocument();
    expect(screen.getByText("SPY")).toBeInTheDocument();
  });

  it("shows a real error state with Retry on a hard error", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValue(EMPTY_PORTFOLIO);
    const spy = vi
      .spyOn(api, "getSentimentHistory")
      .mockRejectedValueOnce(new ApiError("boom", 500));

    render(<SentimentMiniChart />);

    expect(await screen.findByText("Couldn't load")).toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();
    expect(screen.queryByTestId("sentimentMini-widget")).not.toBeInTheDocument();

    spy.mockResolvedValueOnce({
      symbol: "AAPL",
      points: [{ date: "2026-08-01", score: 0.1 }],
      reason: null,
    });
    screen.getByRole("button", { name: "Retry" }).click();
    expect(await screen.findByTestId("sentimentMini-widget")).toBeInTheDocument();
  });

  it("shows the real empty/cold-start state (never a fabricated 0) when points is empty", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValue(EMPTY_PORTFOLIO);
    vi.spyOn(api, "getSentimentHistory").mockResolvedValue({
      symbol: "AAPL",
      points: [],
      reason: "No archived sentiment history for AAPL yet.",
    });

    render(<SentimentMiniChart />);

    expect(
      await screen.findByText("No sentiment history yet for AAPL")
    ).toBeInTheDocument();
    expect(
      screen.getByText("No archived sentiment history for AAPL yet.")
    ).toBeInTheDocument();
    expect(screen.queryByTestId("sentimentMini-widget")).not.toBeInTheDocument();
  });

  it("treats all-null scores (no real archived days) as the empty state, not a blank chart", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValue(EMPTY_PORTFOLIO);
    vi.spyOn(api, "getSentimentHistory").mockResolvedValue({
      symbol: "AAPL",
      points: [
        { date: "2026-08-01", score: null },
        { date: "2026-08-02", score: null },
      ],
      reason: null,
    });

    render(<SentimentMiniChart />);

    expect(
      await screen.findByText("No sentiment history yet for AAPL")
    ).toBeInTheDocument();
  });
});

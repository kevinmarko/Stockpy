/**
 * Portfolio.test.tsx — renders against the real mock API. Covers the
 * account-truth tiles, the "not fabricated" empty-follows state, and that
 * an unavailable account snapshot renders the honest error state rather than
 * a fabricated $0 portfolio.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Portfolio } from "./Portfolio";
import { api } from "../api/client";
import { ApiError } from "../api/types";

function renderPortfolio() {
  return render(
    <MemoryRouter>
      <Portfolio />
    </MemoryRouter>
  );
}

describe("Portfolio screen (real mock API)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders total equity, buying power, dividends, and positions from the real mock account", async () => {
    renderPortfolio();

    expect(await screen.findByText("Total equity")).toBeInTheDocument();
    expect(screen.getByText("Buying power")).toBeInTheDocument();
    expect(screen.getByText("Dividends")).toBeInTheDocument();
    // "Positions" appears both as a summary tile label and the section heading.
    expect(screen.getAllByText("Positions").length).toBeGreaterThan(0);
  });

  it("an unavailable account snapshot renders the honest error state, never a fabricated $0 portfolio", async () => {
    vi.spyOn(api, "getPortfolio").mockRejectedValueOnce(
      new ApiError("no account snapshot cached yet", 404)
    );

    renderPortfolio();

    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
    // Never falls through to render tiles with fabricated zero values.
    expect(screen.queryByText("Total equity")).not.toBeInTheDocument();
  });

  it("renders the realized-performance section from broker order history", async () => {
    renderPortfolio();
    expect(await screen.findByText("Realized performance")).toBeInTheDocument();
    expect(screen.getByText("Win rate")).toBeInTheDocument();
    expect(screen.getByText("Profit factor")).toBeInTheDocument();
  });

  it("no cached realized trades renders the honest empty state, not a fabricated win rate", async () => {
    vi.spyOn(api, "getRealized").mockResolvedValueOnce({
      summary: {
        n_trades: 0,
        total_realized_pnl: 0,
        win_rate: null,
        avg_win: null,
        avg_loss: null,
        profit_factor: null,
        avg_return_pct: null,
        avg_holding_days: null,
        best_trade_pnl: null,
        worst_trade_pnl: null,
        gross_profit: 0,
        gross_loss: 0,
      },
      trades: [],
      n_fills: 0,
      available: false,
    });
    renderPortfolio();
    expect(await screen.findByText("No realized trades cached yet.")).toBeInTheDocument();
  });

  it("no active follows renders the honest empty state with a link back to the marketplace, not a fabricated follow", async () => {
    vi.spyOn(api, "getFollows").mockResolvedValueOnce([]);

    renderPortfolio();

    expect(await screen.findByText("You aren't following any Pilots yet.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Browse Pilots" })).toBeInTheDocument();
  });
});

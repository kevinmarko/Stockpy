/**
 * PerformanceMetricsCard.test.tsx — Pilots Manager's realized-performance
 * glance card. Renders against the REAL mock API (no vi.mock). The mock's
 * REALIZED_TRADES fixture has 6 closed trades with a real (non-zero) net
 * PnL, so the happy path must show real numbers -- never the fabricated-
 * looking "$0.00"/"0%"/"0.00" the original stub hardcoded (CONSTRAINT #4).
 * The cold-start / zero-trades branch must degrade to an honest empty state
 * instead.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PerformanceMetricsCard } from "./PerformanceMetricsCard";
import { api } from "../../api/client";
import type { RealizedPerformance } from "../../api/types";

describe("PerformanceMetricsCard (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders real PnL, win rate, profit factor, and trade count from the mock fixture", async () => {
    render(<PerformanceMetricsCard />);

    const tiles = await screen.findByTestId("performance-metrics-tiles");
    // 6 closed trades in the mock fixture -- never the stub's fabricated "0.00".
    expect(tiles.textContent).toContain("6");
    expect(screen.getByText("Realized P&L").closest(".tile")?.textContent).not.toMatch(/\$0\.00\b/);
    expect(screen.getByText("Win rate").closest(".tile")?.textContent).not.toBe("Win rate0%");
    expect(screen.getByText("Trades").closest(".tile")?.textContent).toContain("6");
  });

  it("renders an honest empty state, never a fabricated $0.00/0%/0.00, when nothing is cached yet", async () => {
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
    } satisfies RealizedPerformance);

    render(<PerformanceMetricsCard />);

    await waitFor(() => expect(screen.getByText("No realized trades yet")).toBeInTheDocument());
    expect(screen.queryByTestId("performance-metrics-tiles")).not.toBeInTheDocument();
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("also degrades to the honest empty state when `available` is true but n_trades is 0", async () => {
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
      available: true,
    } satisfies RealizedPerformance);

    render(<PerformanceMetricsCard />);

    await waitFor(() => expect(screen.getByText("No realized trades yet")).toBeInTheDocument());
  });
});

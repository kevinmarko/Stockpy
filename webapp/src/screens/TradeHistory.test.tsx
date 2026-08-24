/**
 * TradeHistory.test.tsx — the durable, paginated broker closed-trade
 * history screen. Exercises the real mock fixture (happy path + symbol
 * filter) plus the honesty branches (cold-start "not ingested yet", a null
 * return_pct/holding_days/quantity rendering "—" not "0"/"NaN") via direct
 * api spies, matching this codebase's established convention for exercising
 * backend honesty contracts (see Portfolio.test.tsx's getRealized spy).
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TradeHistory } from "./TradeHistory";
import { api } from "../api/client";
import type { TradeHistoryPage } from "../api/types";

function renderScreen() {
  return render(
    <MemoryRouter>
      <TradeHistory />
    </MemoryRouter>
  );
}

function emptyPage(overrides: Partial<TradeHistoryPage> = {}): TradeHistoryPage {
  return {
    trades: [],
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
    total: 0,
    limit: 25,
    offset: 0,
    symbols: [],
    available: false,
    source: "durable_store",
    last_ingested_at: null,
    ...overrides,
  };
}

describe("TradeHistory screen (real mock API)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the tab guide", async () => {
    renderScreen();
    await waitFor(() => {
      expect(screen.getByTestId("tab-guide-trade-history")).toBeInTheDocument();
    });
  });

  it("renders summary tiles and trade rows from the mock fixture", async () => {
    renderScreen();
    expect(await screen.findByText("Win rate")).toBeInTheDocument();
    expect(screen.getByText("Profit factor")).toBeInTheDocument();
    // NVDA is in REALIZED_TRADES, which TRADE_HISTORY_TRADES extends --
    // it appears both as a table row link and a filter-dropdown option, so
    // scope to the row link specifically.
    expect(await screen.findByRole("link", { name: "NVDA" })).toBeInTheDocument();
  });

  it("cold start (nothing ingested yet) shows the honest empty state", async () => {
    vi.spyOn(api, "getTradeHistory").mockResolvedValueOnce(emptyPage());
    renderScreen();
    expect(await screen.findByText("No trade history ingested yet")).toBeInTheDocument();
    expect(
      screen.getByText(/Run `python3 main.py --refresh-account`/)
    ).toBeInTheDocument();
  });

  it("a null quantity/return_pct/holding_days renders —, never 0 or NaN", async () => {
    vi.spyOn(api, "getTradeHistory").mockResolvedValueOnce(
      emptyPage({
        available: true,
        total: 1,
        symbols: ["ZZZ"],
        summary: {
          n_trades: 1,
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
        trades: [
          {
            symbol: "ZZZ",
            quantity: null,
            entry_ts: null,
            exit_ts: null,
            entry_price: null,
            exit_price: null,
            realized_pnl: null,
            return_pct: null,
            holding_days: null,
          },
        ],
      })
    );
    renderScreen();
    const row = await screen.findByRole("link", { name: "ZZZ" });
    const tr = row.closest("tr");
    expect(tr).not.toBeNull();
    // Every "—" cell -- none of the null fields render "0"/"NaN".
    const dashes = within(tr as HTMLElement).getAllByText("—");
    expect(dashes.length).toBeGreaterThanOrEqual(4); // shares, held, return, P&L
    expect(within(tr as HTMLElement).queryByText("0")).not.toBeInTheDocument();
    expect(within(tr as HTMLElement).queryByText("NaN")).not.toBeInTheDocument();
  });

  it("symbol filter narrows the request and resets to page 1", async () => {
    const spy = vi.spyOn(api, "getTradeHistory");
    renderScreen();
    await screen.findByRole("link", { name: "NVDA" });

    fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "AAPL" } });

    await waitFor(() => {
      const lastCall = spy.mock.calls[spy.mock.calls.length - 1]?.[0];
      expect(lastCall).toMatchObject({ symbol: "AAPL", offset: 0 });
    });
  });

  it("pagination: Next advances the offset, Previous is disabled on page 1", async () => {
    const spy = vi.spyOn(api, "getTradeHistory").mockImplementation(
      async ({ offset = 0 } = {}) =>
        emptyPage({
          available: true,
          total: 60,
          limit: 25,
          offset,
          symbols: ["AAPL"],
          trades: [
            {
              symbol: "AAPL",
              quantity: 1,
              entry_ts: "2026-01-01T00:00:00Z",
              exit_ts: "2026-01-02T00:00:00Z",
              entry_price: 100,
              exit_price: 110,
              realized_pnl: 10,
              return_pct: 10,
              holding_days: 1,
            },
          ],
        })
    );
    renderScreen();
    await screen.findByText(/1–25 of 60/);

    expect(screen.getByText("Previous")).toBeDisabled();
    fireEvent.click(screen.getByText("Next"));

    await waitFor(() => {
      const lastCall = spy.mock.calls[spy.mock.calls.length - 1]?.[0];
      expect(lastCall).toMatchObject({ offset: 25 });
    });
    await screen.findByText(/26–50 of 60/);
    expect(screen.getByText("Previous")).not.toBeDisabled();
  });
});

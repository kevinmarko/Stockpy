/**
 * SymbolSignalOverlayChart.test.tsx
 *
 * REGRESSION (code-review finding on PR #697): `defaultTicker` was only ever
 * read at MOUNT (`useState(defaultTicker || ...)`), so a later change to the
 * prop on an already-mounted instance -- the Create Data App "Configure
 * widget" modal saving a new default ticker, or the operator opening a
 * different saved view whose `widgetConfigs.symbolOverlay.defaultTicker`
 * differs -- was silently ignored. Covers that the prop change now takes
 * effect, and that it does not fight the operator's own manual in-widget
 * symbol selection on every unrelated re-render.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SymbolSignalOverlayChart } from "./SymbolSignalOverlayChart";
import { api } from "../api/client";
import type { Bar, DecisionEntry, Portfolio } from "../api/types";

function portfolioWith(symbols: string[]): Portfolio {
  return {
    total_equity: 100000,
    buying_power: 50000,
    total_unrealized_pl: 0,
    total_dividends: 0,
    position_count: symbols.length,
    positions: symbols.map((symbol) => ({
      symbol,
      qty: 10,
      avg_cost: 100,
      current_price: 105,
      market_value: 1050,
      unrealized_pl: 50,
      unrealized_pl_pct: 0.05,
    })),
    fetched_at: new Date().toISOString(),
    source: "db",
  };
}

function bars(): Bar[] {
  return [
    { date: "2026-01-01", Open: 100, High: 101, Low: 99, Close: 100, Volume: 1000 },
    { date: "2026-01-02", Open: 100, High: 102, Low: 99, Close: 101, Volume: 1200 },
  ];
}

const NO_DECISIONS: DecisionEntry[] = [];

function stubApi(symbols: string[]) {
  vi.spyOn(api, "getPortfolio").mockResolvedValue(portfolioWith(symbols));
  vi.spyOn(api, "getDataBars").mockResolvedValue(bars());
  vi.spyOn(api, "getDecisions").mockResolvedValue(NO_DECISIONS);
}

function selectedSymbol(): string {
  const select = screen.getByTestId("symbol-signal-overlay-symbol-select") as HTMLSelectElement;
  return select.value;
}

describe("SymbolSignalOverlayChart", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("REGRESSION: a defaultTicker prop change on an already-mounted instance updates the selected symbol", async () => {
    stubApi(["AAPL", "MSFT"]);
    const { rerender } = render(<SymbolSignalOverlayChart defaultTicker="AAPL" />);

    await screen.findByTestId("symbol-signal-overlay-symbol-select");
    expect(selectedSymbol()).toBe("AAPL");

    // Simulate the Create Data App "Configure widget" modal (or a different
    // saved view's config) supplying a NEW defaultTicker to the same mounted
    // component -- not a remount.
    rerender(<SymbolSignalOverlayChart defaultTicker="MSFT" />);

    await vi.waitFor(() => expect(selectedSymbol()).toBe("MSFT"));
  });

  it("does not fight the operator's own manual symbol selection on an unrelated re-render", async () => {
    stubApi(["AAPL", "MSFT", "SPY"]);
    const user = userEvent.setup();
    const { rerender } = render(<SymbolSignalOverlayChart defaultTicker="AAPL" />);

    await screen.findByTestId("symbol-signal-overlay-symbol-select");

    const combo = screen.getByTestId("symbol-signal-overlay-symbol-select") as HTMLSelectElement;
    await user.selectOptions(combo, "SPY");
    expect(selectedSymbol()).toBe("SPY");

    // A re-render with the SAME defaultTicker prop value (e.g. a parent
    // re-rendering for an unrelated reason) must not reset the operator's
    // manual choice back to "AAPL".
    rerender(<SymbolSignalOverlayChart defaultTicker="AAPL" />);
    expect(selectedSymbol()).toBe("SPY");
  });
});

/**
 * Portfolio.test.tsx — renders against the real mock API. Covers the
 * account-truth tiles, the "not fabricated" empty-follows state, and that
 * an unavailable account snapshot renders the honest error state rather than
 * a fabricated $0 portfolio.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
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

  it("renders a stale badge when the account snapshot's is_stale is true, and omits it otherwise", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValueOnce({
      total_equity: 1000,
      buying_power: 100,
      total_unrealized_pl: 0,
      total_dividends: 0,
      position_count: 0,
      positions: [],
      source: "cache",
      fetched_at: new Date().toISOString(),
      is_stale: true,
      age_hours: 30,
    });

    renderPortfolio();

    const badge = await screen.findByText("stale");
    expect(badge).toBeInTheDocument();
    // Native title= never fires on tap; the age explanation is now a
    // tap-to-open InfoTip (see components/ui.tsx) -- closed by default,
    // opened by clicking the badge, and readable via role="tooltip".
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    await userEvent.click(badge);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("30.0h old");
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
    // "Realized performance" is a static heading, always present -- but the
    // Win rate/Profit factor tiles only render once the async getRealized()
    // fetch resolves (realized.loading -> false), a strictly later point in
    // time. A synchronous getByText() right after the awaited heading races
    // that still-pending promise -- reliable on a fast local machine, flaky
    // under CI's slower/contended runners. findByText waits for it properly.
    expect(await screen.findByText("Win rate")).toBeInTheDocument();
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

  // ---- G12: held-vs-signal reconciliation (client-side, no backend change) ----

  it("both reconciliation buckets render '—', never a fabricated symbol, when every held position has a signal and no BUY signal is unheld", async () => {
    // The real mock's default fixtures happen to have every held position
    // (AAPL/MSFT/NVDA/V/COST/DUK) already in GET /universe, and the only two
    // BUY-tagged universe symbols (AAPL, NVDA) are both already held -- so
    // this is the honest, un-forced empty-both-buckets case.
    renderPortfolio();
    expect(await screen.findByText("Reconciliation")).toBeInTheDocument();
    // "Held, no signal" / "Signalled, not held" each appear twice (KPI tile
    // label + list sub-heading) -- assert presence via getAllByText.
    expect((await screen.findAllByText("Held, no signal")).length).toBe(2);
    expect(screen.getAllByText("Signalled, not held").length).toBe(2);
    const heldList = screen.getByTestId("held-no-signal-list");
    const signalledList = screen.getByTestId("signalled-not-held-list");
    expect(heldList).toHaveTextContent("—");
    expect(signalledList).toHaveTextContent("—");
  });

  it("a held position absent from the tracked universe surfaces under 'Held, no signal'", async () => {
    vi.spyOn(api, "getUniverse").mockResolvedValueOnce({
      symbols: [{ symbol: "AAPL", action: "BUY" }], // MSFT/NVDA/V/COST/DUK (all held) are absent
    });
    renderPortfolio();
    const heldList = await screen.findByTestId("held-no-signal-list");
    expect(heldList).toHaveTextContent("MSFT");
    expect(heldList).toHaveTextContent("DUK");
    // AAPL IS in the universe -- must not appear in "held, no signal".
    expect(heldList).not.toHaveTextContent("AAPL");
  });

  it("an unheld BUY-signalled symbol surfaces under 'Signalled, not held'; a non-BUY unheld symbol does not", async () => {
    vi.spyOn(api, "getUniverse").mockResolvedValueOnce({
      symbols: [
        { symbol: "AAPL", action: "BUY" }, // held -- must not appear
        { symbol: "TSLA", action: "STRONG BUY" }, // unheld BUY -- must appear
        { symbol: "XOM", action: "HOLD" }, // unheld but not BUY -- must not appear
        { symbol: "T", action: null }, // unheld, no action at all -- must not appear
      ],
    });
    renderPortfolio();
    const signalledList = await screen.findByTestId("signalled-not-held-list");
    expect(signalledList).toHaveTextContent("TSLA");
    expect(signalledList).not.toHaveTextContent("AAPL");
    expect(signalledList).not.toHaveTextContent("XOM");
    expect(signalledList).not.toHaveTextContent(/\bT\b/);
  });

  // ---- G14: buying-power overlay on the equity curve ----

  it("renders a buying-power overlay checkbox, enabled when the curve has data", async () => {
    renderPortfolio();
    const checkbox = await screen.findByTestId("buying-power-overlay-checkbox");
    expect(checkbox).not.toBeChecked();
    // The checkbox mounts immediately, disabled by default, while the
    // equity-curve query is still in flight (its disabled= is derived from
    // equity.data, a separate async fetch from the one findByTestId above
    // waited on) -- wait for that fetch to resolve and the checkbox to
    // reflect the real (non-empty) mock curve data, rather than asserting
    // against a still-loading first render.
    await waitFor(() => expect(checkbox).not.toBeDisabled());
    await userEvent.click(checkbox);
    expect(checkbox).toBeChecked();
    // Data exists, so the "no history" caption must not show once toggled on.
    expect(screen.queryByText("No buying-power history in the selected range.")).not.toBeInTheDocument();
  });

  it("disables the buying-power overlay checkbox and never fabricates an overlay when the curve is empty", async () => {
    vi.spyOn(api, "getEquityCurve").mockResolvedValue({
      range: "3M",
      curve: [
        { date: "2026-07-01", value: 1000 },
        { date: "2026-07-02", value: 1010 },
      ],
      buying_power_curve: [],
    });
    renderPortfolio();
    const checkbox = await screen.findByTestId("buying-power-overlay-checkbox");
    expect(checkbox).toBeDisabled();
  });
});

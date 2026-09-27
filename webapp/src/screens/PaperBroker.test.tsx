import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router";
import { PaperBroker } from "./PaperBroker";
import { api } from "../api/client";

vi.mock("../api/client", async (importOriginal) => {
  // Real `ApiError` re-export preserved (rather than a fully synthetic mock)
  // so `useApi`'s `e instanceof ApiError` check keeps working for the
  // core-hook loading/error UI tests below, which reject with plain Errors.
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ApiError: actual.ApiError,
    api: {
      getPaperBrokerAccount: vi.fn(),
      getPaperBrokerPositions: vi.fn(),
      getPaperBrokerOrders: vi.fn(),
      getPaperBrokerClosedTrades: vi.fn(),
      resetPaperBroker: vi.fn(),
      getThresholds: vi.fn(() => Promise.resolve({ VRP: 0, MAX_KELLY: 0, VIX_HIGH: 0, OPTION_MIN_IVR: 0, REGIME_LOOKAHEAD_DAYS: 0 })),
      // Quick Trade (any FMP-quotable symbol) + the SymbolInput it uses.
      getDataQuotes: vi.fn(),
      postPaperEquityOrder: vi.fn(),
      watchCandidate: vi.fn(),
      getUniverse: vi.fn(),
      // SymbolInput's "not yet tracked" FMP section.
      getSymbolSearch: vi.fn(),
      triggerSymbolBackfill: vi.fn(),
    },
  };
});

describe("PaperBroker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default: no closed trades. Individual tests override this to assert
    // populated/loading/error states for the Closed Trades section.
    vi.mocked(api.getPaperBrokerClosedTrades).mockResolvedValue([]);
    // SymbolInput's shared universe fetch -- give it a default resolved value
    // so its lazy `loadUniverse()` effect never hits an unmocked call.
    vi.mocked(api.getUniverse).mockResolvedValue({ symbols: [] });
    vi.mocked(api.getSymbolSearch).mockResolvedValue({ query: "", results: [], reason: null });
  });


  it("renders account, positions, and orders", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 105000,
      cash: 50000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([
      {
        symbol: "AAPL",
        qty: 100,
        avg_cost: 150,
        current_price: 155,
        market_value: 15500,
        unrealized_pl: 500,
        unrealized_pl_pct: 0.0333,
        strategy_id: "strategy_A",
        pilot_id: null,
        experiment_arm: null,
      },
    ]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([
      {
        symbol: "AAPL",
        side: "BUY",
        qty: 100,
        status: "filled",
        filled_qty: 100,
        filled_avg_price: 150,
        order_id: "123",
        price: 150,
        created_at: "2026-08-12T00:00:00Z",
        strategy_id: "strategy_A",
        pilot_id: null,
        experiment_arm: null,
      },
    ]);
    vi.mocked(api.getPaperBrokerClosedTrades).mockResolvedValue([
      {
        trade_id: 1,
        strategy_id: "untagged",
        pilot_id: null,
        experiment_arm: null,
        symbol: "TSLA",
        side: "BUY",
        qty: 5,
        entry_ts: "2026-08-10T00:00:00Z",
        entry_price: 200,
        exit_ts: "2026-08-11T00:00:00Z",
        exit_price: 220,
        commission: 0,
        realized_pnl: 100,
        realized_pnl_pct: 0.1,
        holding_period_days: 1,
        close_reason: "flatten",
        leg_group_id: null,
      },
    ]);

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    // Summary cards
    expect(await screen.findByText("$105,000.00")).toBeInTheDocument();
    expect(screen.getByText("$50,000.00")).toBeInTheDocument();

    // Positions
    expect(screen.getAllByText("AAPL")).toHaveLength(2); // Position and order
    expect(screen.getByText("$155.00")).toBeInTheDocument(); // current price
    expect(screen.getAllByText("strategy_A")).toHaveLength(2); // Position and order attribution

    // Orders
    expect(screen.getAllByText("BUY")).toHaveLength(2); // Order and closed trade

    // Closed Trades
    expect(await screen.findByText("TSLA")).toBeInTheDocument();
    expect(screen.getByText("untagged")).toBeInTheDocument();
    expect(screen.getByText("$100.00")).toBeInTheDocument(); // realized P&L
    expect(screen.getByText("10.00%")).toBeInTheDocument(); // realized P&L %
  });

  it("opens reset modal and calls reset", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 105000,
      cash: 50000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    vi.mocked(api.resetPaperBroker).mockResolvedValue({ status: "reset", cash: 100000 });

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    expect(await screen.findByText("$105,000.00")).toBeInTheDocument();

    const resetBtn = screen.getByText("Reset Paper Account");
    fireEvent.click(resetBtn);

    expect(screen.getByText("Reset Paper Broker", { selector: "h2" })).toBeInTheDocument();

    const confirmBtn = screen.getByRole("button", { name: "Reset" });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(api.resetPaperBroker).toHaveBeenCalledWith(100000);
    });
  });

  it("renders no options-desk surface (the options desk is archived)", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 105000,
      cash: 50000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    expect(await screen.findByText("$105,000.00")).toBeInTheDocument();
    for (const gone of [
      /Portfolio Risk & Aggregate Greeks/i,
      /Automated Strategy Options Execution/i,
      /Settle Expired Options/i,
      /Options Meta-Labeler/i,
      /Options Strategy Backtest/i,
      /View Options Chain/i,
      /Delta \(Δ\)/,
    ]) {
      expect(screen.queryByText(gone)).not.toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: /Roll/ })).not.toBeInTheDocument();
  });

  it("quick trade: shows an honest note that this is manual/paper-only and doesn't drive Autopilot", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 105000,
      cash: 50000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    expect(await screen.findByText("$105,000.00")).toBeInTheDocument();
    expect(screen.getByText("🔍 Quick Trade — Any Symbol")).toBeInTheDocument();
    expect(
      screen.getByText(/Autopilot's automated signals only act on symbols in your tracked universe/i)
    ).toBeInTheDocument();
  });

  it("quick trade: fetches a quote for an arbitrary symbol and opens the order ticket", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 105000,
      cash: 50000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    vi.mocked(api.getDataQuotes).mockResolvedValue({
      ZZZZ: { symbol: "ZZZZ", price: 42.5, bid: 42.4, ask: 42.6, timestamp: "2026-08-20T14:00:00Z", is_stale: false, source: "fmp" },
    });
    // Quick Trade's SymbolInput requires an exact recognized-symbol match
    // before "Get Quote" is enabled -- "ZZZZ" isn't tracked, so it must come
    // back from the (debounced) FMP symbol-search lookup to be accepted.
    vi.mocked(api.getSymbolSearch).mockImplementation((q) =>
      Promise.resolve(
        q.toUpperCase() === "ZZZZ"
          ? { query: q, results: [{ symbol: "ZZZZ", name: "Arbitrary Corp", currency: "USD", exchange: "NASDAQ", exchange_full_name: null }], reason: null }
          : { query: q, results: [], reason: null }
      )
    );

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    expect(await screen.findByText("$105,000.00")).toBeInTheDocument();

    const input = screen.getByTestId("quick-trade-symbol-input");
    fireEvent.change(input, { target: { value: "zzzz" } });

    // "Get Quote" only becomes enabled once the debounced FMP lookup confirms
    // "ZZZZ" is a real, quotable symbol.
    const getQuoteBtn = await screen.findByText("Get Quote");
    await waitFor(() => {
      expect(getQuoteBtn).not.toBeDisabled();
    });
    fireEvent.click(getQuoteBtn);

    await waitFor(() => {
      expect(api.getDataQuotes).toHaveBeenCalledWith(["ZZZZ"]);
    });

    // Order ticket opens for the arbitrary (untracked) symbol, seeded with
    // the real fetched quote -- not a fabricated price.
    expect(await screen.findByText("Buy ZZZZ Stock")).toBeInTheDocument();
  });

  it("quick trade: auto-fetches a quote as soon as a recognized ticker is typed, with no manual \"Get Quote\" click", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 105000,
      cash: 50000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    vi.mocked(api.getDataQuotes).mockResolvedValue({
      ZZZZ: { symbol: "ZZZZ", price: 42.5, bid: 42.4, ask: 42.6, timestamp: "2026-08-20T14:00:00Z", is_stale: false, source: "fmp" },
    });
    vi.mocked(api.getSymbolSearch).mockImplementation((q) =>
      Promise.resolve(
        q.toUpperCase() === "ZZZZ"
          ? { query: q, results: [{ symbol: "ZZZZ", name: "Arbitrary Corp", currency: "USD", exchange: "NASDAQ", exchange_full_name: null }], reason: null }
          : { query: q, results: [], reason: null }
      )
    );

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    expect(await screen.findByText("$105,000.00")).toBeInTheDocument();

    const input = screen.getByTestId("quick-trade-symbol-input");
    fireEvent.change(input, { target: { value: "zzzz" } });

    // No click on "Get Quote" anywhere in this test -- finishing a
    // recognized ticker alone must be enough once the debounced FMP lookup
    // confirms it's real.
    await waitFor(() => {
      expect(api.getDataQuotes).toHaveBeenCalledWith(["ZZZZ"]);
    });
    expect(await screen.findByText("Buy ZZZZ Stock")).toBeInTheDocument();
  });

  it("quick trade: greys out \"Get Quote\" for an unrecognized ticker instead of letting a typo submit", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 105000,
      cash: 50000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    // No mock override for getSymbolSearch -- the beforeEach default (empty
    // results) applies, matching a genuinely unrecognized/garbled ticker.

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    expect(await screen.findByText("$105,000.00")).toBeInTheDocument();

    const input = screen.getByTestId("quick-trade-symbol-input");
    fireEvent.change(input, { target: { value: "gbpdzd" } });

    const getQuoteBtn = await screen.findByText("Get Quote");
    await waitFor(() => {
      expect(getQuoteBtn).toBeDisabled();
    });
    expect(screen.getByText(/Not a recognized ticker yet/i)).toBeInTheDocument();

    // Clicking a disabled button is a no-op -- no quote lookup is ever fired.
    fireEvent.click(getQuoteBtn);
    expect(api.getDataQuotes).not.toHaveBeenCalled();
  });

  it("quick trade: a ?quickTradeSymbol= URL param (handoff from SymbolScreener) prefills and fetches on mount", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 105000,
      cash: 50000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    vi.mocked(api.getDataQuotes).mockResolvedValue({
      ZZZZ: { symbol: "ZZZZ", price: 42.5, bid: 42.4, ask: 42.6, timestamp: "2026-08-20T14:00:00Z", is_stale: false, source: "fmp" },
    });

    render(
      <MemoryRouter initialEntries={["/paper-broker?quickTradeSymbol=zzzz"]}>
        <PaperBroker />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(api.getDataQuotes).toHaveBeenCalledWith(["ZZZZ"]);
    });
    expect(await screen.findByText("Buy ZZZZ Stock")).toBeInTheDocument();
    expect(screen.getByTestId("quick-trade-symbol-input")).toHaveValue("ZZZZ");
  });

  it("quick trade: shows an honest error when no live quote is available, never a fabricated $0 ticket", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 105000,
      cash: 50000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    // No entry for the requested symbol -- simulates a real, recognized
    // ticker whose live quote fetch itself comes back empty (e.g. a
    // since-delisted symbol). Recognized via getSymbolSearch so the
    // requireExactMatch gate lets the lookup through in the first place --
    // this test is specifically about the quote-fetch failure path, not the
    // unrecognized-ticker gate (see the "greys out" test above for that).
    vi.mocked(api.getSymbolSearch).mockImplementation((q) =>
      Promise.resolve(
        q.toUpperCase() === "NOSUCH"
          ? { query: q, results: [{ symbol: "NOSUCH", name: "No Such Corp", currency: "USD", exchange: "NASDAQ", exchange_full_name: null }], reason: null }
          : { query: q, results: [], reason: null }
      )
    );
    vi.mocked(api.getDataQuotes).mockResolvedValue({});

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    expect(await screen.findByText("$105,000.00")).toBeInTheDocument();

    const input = screen.getByTestId("quick-trade-symbol-input");
    fireEvent.change(input, { target: { value: "nosuch" } });

    const getQuoteBtn = await screen.findByText("Get Quote");
    await waitFor(() => {
      expect(getQuoteBtn).not.toBeDisabled();
    });
    fireEvent.click(getQuoteBtn);

    expect(await screen.findByText(/No live quote available for "NOSUCH"/i)).toBeInTheDocument();
    expect(screen.queryByText("Buy NOSUCH Stock")).not.toBeInTheDocument();
  });

  describe("core hook loading/error UI", () => {
    // Deliberately never resolved/rejected within the test -- keeps the
    // corresponding useApi hook pinned in its initial `loading: true` state
    // so the loading skeleton assertion is stable.
    function pending<T>(): Promise<T> {
      return new Promise<T>(() => {});
    }

    it("shows a loading placeholder for positions while the fetch is pending", async () => {
      vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
        equity: 100000,
        cash: 100000,
        buying_power: 100000,
      });
      vi.mocked(api.getPaperBrokerPositions).mockReturnValue(pending());
      vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);

      render(
        <MemoryRouter>
          <PaperBroker />
        </MemoryRouter>
      );

      expect(await screen.findByText("Loading positions...")).toBeInTheDocument();
    });

    it("shows an inline error message for positions when the fetch rejects", async () => {
      vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
        equity: 100000,
        cash: 100000,
        buying_power: 100000,
      });
      vi.mocked(api.getPaperBrokerPositions).mockRejectedValue(new Error("positions boom"));
      vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);

      render(
        <MemoryRouter>
          <PaperBroker />
        </MemoryRouter>
      );

      expect(await screen.findByText(/Failed to load positions: positions boom/i)).toBeInTheDocument();
    });

    it("shows a loading placeholder for closed trades while the fetch is pending", async () => {
      vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
        equity: 100000,
        cash: 100000,
        buying_power: 100000,
      });
      vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
      vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
      vi.mocked(api.getPaperBrokerClosedTrades).mockReturnValue(pending());

      render(
        <MemoryRouter>
          <PaperBroker />
        </MemoryRouter>
      );

      expect(await screen.findByText("Loading closed trades...")).toBeInTheDocument();
    });

    it("shows an inline error message for closed trades when the fetch rejects", async () => {
      vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
        equity: 100000,
        cash: 100000,
        buying_power: 100000,
      });
      vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
      vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
      vi.mocked(api.getPaperBrokerClosedTrades).mockRejectedValue(new Error("closed trades boom"));

      render(
        <MemoryRouter>
          <PaperBroker />
        </MemoryRouter>
      );

      expect(await screen.findByText(/Failed to load closed trades: closed trades boom/i)).toBeInTheDocument();
    });

    it("shows a loading placeholder for the account summary while the fetch is pending", async () => {
      vi.mocked(api.getPaperBrokerAccount).mockReturnValue(pending());
      vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
      vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);

      render(
        <MemoryRouter>
          <PaperBroker />
        </MemoryRouter>
      );

      expect(await screen.findByText("Loading account summary...")).toBeInTheDocument();
    });
  });
});

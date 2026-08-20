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
      resetPaperBroker: vi.fn(),
      getStrategyOptionsCandidates: vi.fn(),
      executeStrategyOptions: vi.fn(),
      getPaperBrokerGreeks: vi.fn(),
      getOptionsMetaModelStatus: vi.fn(),
      retrainOptionsMetaModel: vi.fn(),
      runOptionsBacktest: vi.fn(),
      settleExpiredPaperOptions: vi.fn(),
      getVolSurface: vi.fn(),
      getScenarioMatrix: vi.fn(),
      getDeltaHedgePreview: vi.fn(),
      executeDeltaHedge: vi.fn(),
      managePaperOptionsExits: vi.fn(),
      rollPaperOptionPosition: vi.fn(),
      getEarningsCrushCandidates: vi.fn(),
      executeEarningsCrushTrade: vi.fn(),
      getUnusualOptionsFlow: vi.fn(),
      getOptionsFlowSentiment: vi.fn(),
      getThresholds: vi.fn(() => Promise.resolve({ VRP: 0, MAX_KELLY: 0, VIX_HIGH: 0, OPTION_MIN_IVR: 0, REGIME_LOOKAHEAD_DAYS: 0 })),
      // Quick Trade (any FMP-quotable symbol) + the SymbolInput it uses.
      getDataQuotes: vi.fn(),
      postOptionsOrder: vi.fn(),
      watchCandidate: vi.fn(),
      getUniverse: vi.fn(),
    },
  };
});

describe("PaperBroker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getOptionsMetaModelStatus).mockResolvedValue({
      n_samples: 1240,
      train_accuracy: 78.5,
      train_roc_auc: 0.812,
      trained_at: "2026-08-14T12:00:00Z",
      enabled: true,
    });
    vi.mocked(api.getStrategyOptionsCandidates).mockResolvedValue({ count: 0, candidates: [] });
    // SymbolInput's shared universe fetch -- give it a default resolved value
    // so its lazy `loadUniverse()` effect never hits an unmocked call.
    vi.mocked(api.getUniverse).mockResolvedValue({ symbols: [] });

    vi.mocked(api.getDeltaHedgePreview).mockResolvedValue({
      symbol: "SPY",
      available: true,
      net_dollar_delta: 24500,
      beta_weighted_delta_spy: 48.5,
      target_hedge_shares: -48.5,
      tolerance_band_shares: 25.0,
      action: "SELL",
      shares: 48,
      required_action: true,
      reason: "Delta imbalance (+48.50 SPY-equiv) exceeds tolerance band (±25.0 shares)",
      spy_spot: 505.20,
    });

    vi.mocked(api.getScenarioMatrix).mockResolvedValue({
      spot_shifts: [-0.1, 0, 0.1],
      iv_shifts: [-0.2, 0, 0.2],
      time_slices: [0, 7],
      matrix: [],
      historical_scenarios: [],
      current_portfolio_value: 100000,
    });

    vi.mocked(api.getVolSurface).mockResolvedValue({
      symbol: "SPY",
      spot_price: 505.20,
      as_of: "2026-08-14T14:00:00Z",
      expirations: ["2026-09-18"],
      smile_points: [],
      term_structure: [],
      skew: {
        skew_25delta: 0.035,
        put_25delta_iv: 0.25,
        call_25delta_iv: 0.21,
        atm_iv: 0.215,
      },
    });

    vi.mocked(api.getPaperBrokerGreeks).mockResolvedValue({
      total_positions: 1,
      stock_positions_count: 1,
      option_positions_count: 0,
      net_delta_shares: 100,
      net_dollar_delta: 15500,
      net_gamma: 0,
      net_theta_daily: 0,
      net_vega_1pct: 0,
      beta_weighted_delta_spy: 31,
      positions: [
        {
          symbol: "AAPL",
          asset_type: "stock",
          base_ticker: "AAPL",
          qty: 100,
          spot_price: 155,
          delta_per_unit: 1,
          gamma_per_unit: 0,
          theta_daily_per_unit: 0,
          vega_1pct_per_unit: 0,
          position_delta: 100,
          position_dollar_delta: 15500,
          position_gamma: 0,
          position_theta_daily: 0,
          position_vega_1pct: 0,
          market_value: 15500,
        },
      ],
    });
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

    // Portfolio Greeks
    expect(screen.getByText(/Portfolio Risk & Aggregate Greeks/i)).toBeInTheDocument();
    expect(screen.getByText("+100.0 sh")).toBeInTheDocument();

    // Positions
    expect(screen.getAllByText("AAPL")).toHaveLength(2); // Position and order
    expect(screen.getByText("$155.00")).toBeInTheDocument(); // current price
    
    // Orders
    expect(screen.getByText("BUY")).toBeInTheDocument();
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

  it("renders strategy option candidates and executes them", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 100000,
      cash: 100000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    vi.mocked(api.getStrategyOptionsCandidates).mockResolvedValue({
      count: 1,
      candidates: [
        {
          symbol: "AAPL",
          strategy: "Put Credit Spread",
          action: "Open",
          net_premium: 1.5,
          ivr: 65,
          trend_bias: "Bullish",
          target_dte: 30,
          legs: [],
        },
      ],
    });
    vi.mocked(api.executeStrategyOptions).mockResolvedValue({
      executed_count: 1,
      skipped_count: 0,
      failed_count: 0,
      executed: [{ symbol: "AAPL", strategy: "Put Credit Spread", contracts: 1, net_price: 1.5, net_cash_impact: 148.7 }],
      skipped: [],
      failed: [],
    });

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    expect(await screen.findByText("Put Credit Spread")).toBeInTheDocument();
    expect(screen.getByText("Execute 1 Strategy Trades")).toBeInTheDocument();

    const execBtn = screen.getByText("Execute 1 Strategy Trades");
    fireEvent.click(execBtn);

    await waitFor(() => {
      expect(api.executeStrategyOptions).toHaveBeenCalled();
    });
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

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    expect(await screen.findByText("$105,000.00")).toBeInTheDocument();

    const input = screen.getByTestId("quick-trade-symbol-input");
    fireEvent.change(input, { target: { value: "zzzz" } });
    fireEvent.click(screen.getByText("Get Quote"));

    await waitFor(() => {
      expect(api.getDataQuotes).toHaveBeenCalledWith(["ZZZZ"]);
    });

    // Order ticket opens for the arbitrary (untracked) symbol, seeded with
    // the real fetched quote -- not a fabricated price.
    expect(await screen.findByText("Buy ZZZZ Stock")).toBeInTheDocument();
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

  it("auto-execute: a ?scanSymbols= URL param (handoff from SymbolScreener) prefills the scan input on mount", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 105000,
      cash: 50000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    vi.mocked(api.getStrategyOptionsCandidates).mockResolvedValue({ count: 0, candidates: [] });

    render(
      <MemoryRouter initialEntries={["/paper-broker?scanSymbols=AAPL,MSFT"]}>
        <PaperBroker />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(api.getStrategyOptionsCandidates).toHaveBeenCalledWith(["AAPL", "MSFT"]);
    });
    expect(screen.getByTestId("scan-symbols-input")).toHaveValue("AAPL,MSFT");
  });

  it("quick trade: shows an honest error when no live quote is available, never a fabricated $0 ticket", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 105000,
      cash: 50000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    // No entry for the requested symbol -- simulates an unquotable/delisted ticker.
    vi.mocked(api.getDataQuotes).mockResolvedValue({});

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    expect(await screen.findByText("$105,000.00")).toBeInTheDocument();

    const input = screen.getByTestId("quick-trade-symbol-input");
    fireEvent.change(input, { target: { value: "nosuch" } });
    fireEvent.click(screen.getByText("Get Quote"));

    expect(await screen.findByText(/No live quote available for "NOSUCH"/i)).toBeInTheDocument();
    expect(screen.queryByText("Buy NOSUCH Stock")).not.toBeInTheDocument();
  });

  it("auto-execute: entering a symbol list scans those symbols instead of WATCHLIST", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 105000,
      cash: 50000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    vi.mocked(api.getStrategyOptionsCandidates).mockResolvedValue({
      count: 1,
      candidates: [
        {
          symbol: "AAPL",
          strategy: "Put Credit Spread",
          action: "Open",
          net_premium: 1.5,
          ivr: 65,
          trend_bias: "Bullish",
          target_dte: 30,
          legs: [],
        },
      ],
    });
    vi.mocked(api.executeStrategyOptions).mockResolvedValue({
      executed_count: 1,
      skipped_count: 0,
      failed_count: 0,
      executed: [{ symbol: "AAPL", strategy: "Put Credit Spread", contracts: 1, net_price: 1.5, net_cash_impact: 148.7 }],
      skipped: [],
      failed: [],
    });

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    expect(await screen.findByText("Put Credit Spread")).toBeInTheDocument();

    // Blank input preserves today's exact default: no `symbols` argument,
    // which the backend resolves to settings.WATCHLIST.
    expect(api.getStrategyOptionsCandidates).toHaveBeenCalledWith(undefined);

    const scanInput = screen.getByTestId("scan-symbols-input");
    fireEvent.change(scanInput, { target: { value: "aapl, msft ,  xom" } });

    await waitFor(() => {
      expect(api.getStrategyOptionsCandidates).toHaveBeenCalledWith(["AAPL", "MSFT", "XOM"]);
    });

    fireEvent.click(screen.getByText(/Execute \d+ Strategy Trades/));

    await waitFor(() => {
      expect(api.executeStrategyOptions).toHaveBeenCalledWith(["AAPL", "MSFT", "XOM"]);
    });
  });

  it("settles expired option contracts when clicked", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 100000,
      cash: 100000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    vi.mocked(api.settleExpiredPaperOptions).mockResolvedValue({
      settled_count: 2,
      settled: [],
    });

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    const settleBtn = await screen.findByText("⏱ Settle Expired Options");
    expect(settleBtn).toBeInTheDocument();
    fireEvent.click(settleBtn);

    await waitFor(() => {
      expect(api.settleExpiredPaperOptions).toHaveBeenCalled();
    });
  });

  it("retrains Stage 4 ML meta-model and runs backtest", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 100000,
      cash: 100000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);

    vi.mocked(api.retrainOptionsMetaModel).mockResolvedValue({
      status: "success",
      trained_samples: 1500,
      accuracy: 82.5,
      roc_auc: 0.85,
      trained_at: "2026-08-14T14:00:00Z",
    });
    vi.mocked(api.runOptionsBacktest).mockResolvedValue({
      strategy_name: "Put Credit Spread",
      ticker: "SPY",
      start_date: "2020-01-01",
      end_date: "2024-01-01",
      initial_capital: 100000,
      final_capital: 125000,
      total_return_pct: 25.0,
      annualized_return_pct: 6.2,
      sharpe_ratio: 1.65,
      sortino_ratio: 2.1,
      max_drawdown_pct: 5.2,
      total_trades: 50,
      winning_trades: 42,
      losing_trades: 8,
      win_rate_pct: 84.0,
      profit_factor: 2.8,
      avg_win: 600,
      avg_loss: 500,
      pbo: 0.08,
      dsr: 0.99,
      passes_stress: true,
      deployable: true,
      equity_curve: [],
      trades: [],
    });

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    // Test retrain meta-model button
    const retrainBtn = await screen.findByText("⚡ Retrain Meta-Model");
    expect(retrainBtn).toBeInTheDocument();
    fireEvent.click(retrainBtn);

    await waitFor(() => {
      expect(api.retrainOptionsMetaModel).toHaveBeenCalled();
    });

    // Test backtest run button
    const backtestBtn = screen.getByText("▶ Run Backtest");
    expect(backtestBtn).toBeInTheDocument();
    fireEvent.click(backtestBtn);

    await waitFor(() => {
      expect(api.runOptionsBacktest).toHaveBeenCalled();
    });
  });

  it("evaluates and executes manage exits when clicked", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 100000,
      cash: 100000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    vi.mocked(api.managePaperOptionsExits).mockResolvedValue({
      evaluated_count: 3,
      closed_count: 1,
      closed_positions: [
        {
          symbol: "SPY 2026-09-18 $500.00 PUT",
          qty: -2,
          reason: "PROFIT_TARGET_50",
          pnl_dollar: 340.0,
          pnl_pct: 0.52,
          closed_at_price: 1.20,
        },
      ],
      message: "Closed 1 position reaching 50% profit target.",
    });

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    const manageExitsBtn = await screen.findByText("⚡ Manage Exits");
    expect(manageExitsBtn).toBeInTheDocument();
    fireEvent.click(manageExitsBtn);

    await waitFor(() => {
      expect(api.managePaperOptionsExits).toHaveBeenCalled();
    });
  });

  it("triggers delta hedge rebalance when clicked", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 100000,
      cash: 100000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    vi.mocked(api.executeDeltaHedge).mockResolvedValue({
      ok: true,
      hedged: true,
      order_id: "ord_hedge_123",
      shares: 48,
      symbol: "SPY",
      action: "SELL",
      message: "Sold 48 SPY shares",
    });

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    const hedgeBtn = await screen.findByRole("button", { name: /Execute Delta Hedge/i });
    expect(hedgeBtn).toBeInTheDocument();
    fireEvent.click(hedgeBtn);

    await waitFor(() => {
      expect(api.executeDeltaHedge).toHaveBeenCalled();
    });
  });

  it("opens roll modal and executes roll order on option position", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 100000,
      cash: 100000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([
      {
        symbol: "SPY 2026-09-18 $500.00 PUT",
        qty: -2,
        avg_cost: 2.50,
        current_price: 1.20,
        market_value: -240,
        unrealized_pl: 260,
        unrealized_pl_pct: 0.52,
      },
    ]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    vi.mocked(api.rollPaperOptionPosition).mockResolvedValue({
      ok: true,
      order_id: "ord_roll_123",
      message: "Successfully rolled position",
    });

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    const rollBtn = await screen.findByText("🔄 Roll");
    expect(rollBtn).toBeInTheDocument();
    fireEvent.click(rollBtn);

    // Roll modal opens
    expect(screen.getByText("Roll Option Position", { selector: "h2" })).toBeInTheDocument();

    const confirmRollBtn = screen.getByRole("button", { name: /Confirm & Execute Roll/i });
    fireEvent.click(confirmRollBtn);

    await waitFor(() => {
      expect(api.rollPaperOptionPosition).toHaveBeenCalledWith({
        symbol: "SPY",
        close_legs: [{ symbol: "SPY 2026-09-18 $500.00 PUT", side: "buy", qty: 2 }],
        open_legs: [{ symbol: "SPY 2026-10-16 $500.00 PUT", side: "sell", qty: 2 }],
        contracts: 2,
      });
    });
  });

  it("toggles volatility surface view drawer", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 100000,
      cash: 100000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    const volSurfaceBtn = await screen.findByText("🌊 Vol Surface");
    expect(volSurfaceBtn).toBeInTheDocument();
    fireEvent.click(volSurfaceBtn);

    expect(await screen.findByText(/Volatility Surface & Skew Analytics/i)).toBeInTheDocument();
  });

  it("toggles earnings crush scanner drawer", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 100000,
      cash: 100000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    vi.mocked(api.getEarningsCrushCandidates).mockResolvedValue({ count: 0, candidates: [] });

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    const crushBtn = await screen.findByText("⚡ Earnings Crush");
    expect(crushBtn).toBeInTheDocument();
    fireEvent.click(crushBtn);

    expect(await screen.findByText(/Earnings Volatility Crush Scanner/i)).toBeInTheDocument();
  });

  it("toggles unusual options flow feed drawer", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 100000,
      cash: 100000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    vi.mocked(api.getUnusualOptionsFlow).mockResolvedValue({ count: 0, trades: [] });
    vi.mocked(api.getOptionsFlowSentiment).mockResolvedValue({
      sentiment: {
        symbol: "NVDA",
        sentiment_score: 0.5,
        bullish_notional: 1000000,
        bearish_notional: 500000,
        total_notional: 1500000,
        call_volume: 5000,
        put_volume: 2500,
        put_call_ratio: 0.5,
      },
    });

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    const flowBtn = await screen.findByText("🌊 Unusual Flow");
    expect(flowBtn).toBeInTheDocument();
    fireEvent.click(flowBtn);

    expect(await screen.findByText(/Unusual Options Activity & Order Flow Feed/i)).toBeInTheDocument();
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

    it("shows a loading placeholder for portfolio Greeks while the fetch is pending", async () => {
      vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
        equity: 100000,
        cash: 100000,
        buying_power: 100000,
      });
      vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
      vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
      vi.mocked(api.getPaperBrokerGreeks).mockReturnValue(pending());

      render(
        <MemoryRouter>
          <PaperBroker />
        </MemoryRouter>
      );

      expect(await screen.findByText("Loading portfolio risk & Greeks...")).toBeInTheDocument();
      expect(screen.queryByText(/Portfolio Risk & Aggregate Greeks/i)).not.toBeInTheDocument();
    });

    it("shows an inline error message for portfolio Greeks when the fetch rejects", async () => {
      vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
        equity: 100000,
        cash: 100000,
        buying_power: 100000,
      });
      vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
      vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
      vi.mocked(api.getPaperBrokerGreeks).mockRejectedValue(new Error("greeks boom"));

      render(
        <MemoryRouter>
          <PaperBroker />
        </MemoryRouter>
      );

      expect(await screen.findByText(/Failed to load portfolio risk & Greeks: greeks boom/i)).toBeInTheDocument();
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

    it("shows an inline error message when the strategy options candidates fetch rejects", async () => {
      vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
        equity: 100000,
        cash: 100000,
        buying_power: 100000,
      });
      vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
      vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
      vi.mocked(api.getStrategyOptionsCandidates).mockRejectedValue(new Error("candidates boom"));

      render(
        <MemoryRouter>
          <PaperBroker />
        </MemoryRouter>
      );

      expect(
        await screen.findByText(/Failed to load strategy options candidates: candidates boom/i)
      ).toBeInTheDocument();
    });
  });
});


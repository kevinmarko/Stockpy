import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { DispersionScanner } from "./DispersionScanner";
import { api } from "../../api/client";
import type { DispersionBasketResponse } from "../../api/types";

vi.mock("../../api/client", () => ({
  api: {
    getDispersionOpportunities: vi.fn(),
    executeDispersionBasket: vi.fn(),
  },
}));

const mockDispersionResponse: DispersionBasketResponse = {
  opportunities: [
    {
      id: "disp_qqq_1",
      index_symbol: "QQQ",
      index_name: "Invesco QQQ Trust",
      index_spot: 480.20,
      index_iv: 0.215,
      index_rv_30d: 0.152,
      index_straddle_strike: 480.0,
      index_straddle_price: 18.50,
      index_straddle_contracts: 10,
      index_action: "SELL",
      implied_correlation: 0.68,
      realized_correlation: 0.44,
      correlation_spread: 0.24,
      regime: "LONG_DISPERSION",
      trade_recommendation: "Rich Implied Correlation (+24.0% spread). Sell 10x QQQ Straddles, Buy Vega-Neutral Constituent Straddles.",
      index_vega_total: 340.0,
      constituents_vega_total: 343.4,
      net_vega: 3.4,
      vega_neutrality_ratio: 1.01,
      net_premium_estimate: 1420.50,
      expiration: "2026-09-18",
      dte: 35,
      constituents: [
        {
          symbol: "AAPL",
          weight: 0.18,
          spot_price: 224.50,
          atm_iv: 0.28,
          realized_vol_30d: 0.20,
          straddle_strike: 225.0,
          straddle_bid: 10.20,
          straddle_ask: 10.60,
          straddle_mid: 10.40,
          vega_per_straddle: 0.32,
          contracts_allocated: 18,
          leg_action: "BUY",
          implied_rv_spread: 0.08,
        },
        {
          symbol: "MSFT",
          weight: 0.16,
          spot_price: 445.00,
          atm_iv: 0.26,
          realized_vol_30d: 0.19,
          straddle_strike: 445.0,
          straddle_bid: 21.00,
          straddle_ask: 21.80,
          straddle_mid: 21.40,
          vega_per_straddle: 0.48,
          contracts_allocated: 12,
          leg_action: "BUY",
          implied_rv_spread: 0.07,
        },
        {
          symbol: "NVDA",
          weight: 0.15,
          spot_price: 128.50,
          atm_iv: 0.48,
          realized_vol_30d: 0.36,
          straddle_strike: 130.0,
          straddle_bid: 11.80,
          straddle_ask: 12.20,
          straddle_mid: 12.00,
          vega_per_straddle: 0.22,
          contracts_allocated: 24,
          leg_action: "BUY",
          implied_rv_spread: 0.12,
        },
      ],
      as_of: "2026-08-14T14:00:00Z",
    },
    {
      id: "disp_spy_1",
      index_symbol: "SPY",
      index_name: "SPDR S&P 500 ETF Trust",
      index_spot: 545.80,
      index_iv: 0.148,
      index_rv_30d: 0.112,
      index_straddle_strike: 545.0,
      index_straddle_price: 14.20,
      index_straddle_contracts: 10,
      index_action: "SELL",
      implied_correlation: 0.62,
      realized_correlation: 0.48,
      correlation_spread: 0.14,
      regime: "NEUTRAL",
      trade_recommendation: "Moderate Implied Correlation (+14.0% spread). Below entry threshold (≥15.0%). Hold / Monitor.",
      index_vega_total: 420.0,
      constituents_vega_total: 418.0,
      net_vega: -2.0,
      vega_neutrality_ratio: 0.995,
      net_premium_estimate: 880.00,
      expiration: "2026-09-18",
      dte: 35,
      constituents: [
        {
          symbol: "MSFT",
          weight: 0.14,
          spot_price: 445.00,
          atm_iv: 0.26,
          realized_vol_30d: 0.19,
          straddle_strike: 445.0,
          straddle_bid: 21.00,
          straddle_ask: 21.80,
          straddle_mid: 21.40,
          vega_per_straddle: 0.48,
          contracts_allocated: 10,
          leg_action: "BUY",
          implied_rv_spread: 0.07,
        },
      ],
      as_of: "2026-08-14T14:00:00Z",
    },
  ],
  count: 2,
  as_of: "2026-08-14T14:00:00Z",
};

describe("DispersionScanner", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getDispersionOpportunities).mockResolvedValue(mockDispersionResponse);
    vi.mocked(api.executeDispersionBasket).mockResolvedValue({
      ok: true,
      basket_id: "bsk_123",
      index_symbol: "QQQ",
      index_order_id: "ord_qqq",
      constituent_order_ids: ["ord_aapl", "ord_msft", "ord_nvda"],
      strategy: "Dispersion Arbitrage",
      net_credit_debit: 1420.50,
      legs_count: 8,
      message: "Successfully executed Dispersion Basket on QQQ. (8 legs executed)",
    });
  });

  it("renders scanner title, correlation spread gauge, and constituent table", async () => {
    render(<DispersionScanner />);

    expect(await screen.findByText(/Options Dispersion & Implied Correlation Scanner/i)).toBeInTheDocument();
    expect(screen.getByText("PAIRWISE CORRELATION SPREAD (Δρ)")).toBeInTheDocument();
    expect(screen.getByText("68.0%")).toBeInTheDocument(); // Implied
    expect(screen.getByText("44.0%")).toBeInTheDocument(); // Realized
    expect(screen.getByText("+24.0%")).toBeInTheDocument(); // Spread
    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("MSFT")).toBeInTheDocument();
    expect(screen.getByText("NVDA")).toBeInTheDocument();
  });

  it("displays vega neutrality ratio and net vega metrics", async () => {
    render(<DispersionScanner />);

    expect(await screen.findByText(/Vega Neutral: 1.01x/i)).toBeInTheDocument();
    expect(screen.getByText("-$340.0")).toBeInTheDocument();
    expect(screen.getByText("+$343.4")).toBeInTheDocument();
    expect(screen.getByText("+$3.4 $/vol")).toBeInTheDocument();
  });

  it("filters opportunities by Long Dispersion regime only", async () => {
    render(<DispersionScanner />);

    await screen.findByText(/Options Dispersion & Implied Correlation Scanner/i);
    const filterBtn = screen.getByRole("button", { name: /Filter: All Regimes/i });
    fireEvent.click(filterBtn);

    expect(screen.getByText(/✓ Long Dispersion \(Δρ ≥ \+15%\) Only/i)).toBeInTheDocument();
  });

  it("filters constituents by search input", async () => {
    render(<DispersionScanner />);

    await screen.findByText("AAPL");
    const searchInput = screen.getByPlaceholderText(/Filter constituents.../i);
    fireEvent.change(searchInput, { target: { value: "NVDA" } });

    expect(screen.getByText("NVDA")).toBeInTheDocument();
  });

  it("executes dispersion basket and triggers callbacks", async () => {
    const onTradeMock = vi.fn();
    render(<DispersionScanner onTradeExecuted={onTradeMock} />);

    const execBtn = await screen.findByRole("button", { name: /⚡ Execute Dispersion Basket/i });
    fireEvent.click(execBtn);

    await waitFor(() => {
      expect(api.executeDispersionBasket).toHaveBeenCalledWith(
        expect.objectContaining({
          opportunity_id: "disp_qqq_1",
          index_symbol: "QQQ",
          regime: "LONG_DISPERSION",
        })
      );
      expect(onTradeMock).toHaveBeenCalledWith(
        expect.objectContaining({
          ok: true,
          index_symbol: "QQQ",
        })
      );
    });

    expect(await screen.findByText(/Successfully executed Dispersion Basket on QQQ/i)).toBeInTheDocument();
  });

  it("calls onClose when close button clicked", async () => {
    const handleClose = vi.fn();
    render(<DispersionScanner onClose={handleClose} />);

    const closeBtn = await screen.findByText("✕ Close");
    fireEvent.click(closeBtn);

    expect(handleClose).toHaveBeenCalled();
  });
});

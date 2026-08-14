import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { GammaScalperView } from "./GammaScalperView";
import { api } from "../../api/client";
import type { GammaScalpResponse } from "../../api/types";

vi.mock("../../api/client", () => ({
  api: {
    simulateGammaScalping: vi.fn(),
  },
}));

const mockScalpResult: GammaScalpResponse = {
  symbol: "SPY",
  spot_price: 505.20,
  initial_delta: 0.50,
  initial_gamma: 0.35,
  initial_theta: -185.0,
  total_trades: 2,
  rebalance_count: 2,
  delta_threshold: 0.15,
  total_pnl: 1420.50,
  gamma_rent_total: 2150.00,
  theta_burn_total: 720.00,
  stock_pnl: 850.00,
  option_pnl: 570.50,
  transaction_costs: 9.50,
  net_edge: 1430.00,
  trades: [
    {
      step: 5,
      timestamp: "T+5h",
      spot_price: 512.40,
      pre_delta: 0.18,
      post_delta: 0.01,
      shares_traded: 170,
      side: "SELL",
      trade_price: 512.40,
      cash_flow: 87108.0,
      stock_position: -670,
      option_mtm: 5400.0,
      total_pnl: 380.0,
      gamma_rent_cumulative: 520.0,
      theta_decay_cumulative: 140.0,
    },
    {
      step: 12,
      timestamp: "T+12h",
      spot_price: 498.20,
      pre_delta: -0.19,
      post_delta: -0.02,
      shares_traded: 180,
      side: "BUY",
      trade_price: 498.20,
      cash_flow: -89676.0,
      stock_position: -490,
      option_mtm: 4800.0,
      total_pnl: 1420.50,
      gamma_rent_cumulative: 2150.0,
      theta_decay_cumulative: 720.0,
    },
  ],
  price_path: [505.20, 507.10, 510.50, 512.40, 508.00, 502.10, 498.20],
  pnl_path: [
    { step: 0, spot: 505.20, total_pnl: 0, gamma_rent: 0, theta_decay: 0, option_mtm: 5000, stock_pnl: 0 },
    { step: 5, spot: 512.40, total_pnl: 380, gamma_rent: 520, theta_decay: 140, option_mtm: 5400, stock_pnl: -160 },
    { step: 12, spot: 498.20, total_pnl: 1420.50, gamma_rent: 2150, theta_decay: 720, option_mtm: 4800, stock_pnl: 850 },
  ],
};

describe("GammaScalperView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.simulateGammaScalping).mockResolvedValue(mockScalpResult);
  });

  it("renders simulator header, parameter controls, and initial simulation results", async () => {
    render(<GammaScalperView initialSymbol="SPY" spotPrice={505.20} />);

    expect(await screen.findByText(/Intraday Gamma Scalping & Delta Neutralization Simulator/i)).toBeInTheDocument();
    expect(screen.getByText("SPY $505.20")).toBeInTheDocument();
    expect(screen.getByText("+$1,420.50")).toBeInTheDocument(); // Net PnL
    expect(screen.getByText("+$2,150.00")).toBeInTheDocument(); // Gamma Rent
    expect(screen.getAllByText("-$720.00").length).toBeGreaterThan(0); // Theta Decay
    expect(screen.getByText("+$1,430.00")).toBeInTheDocument(); // Net Edge
  });

  it("renders hedge rebalance ledger table with trade records", async () => {
    render(<GammaScalperView initialSymbol="SPY" spotPrice={505.20} />);

    expect(await screen.findByText(/Dynamic Delta Rebalancing Ledger/i)).toBeInTheDocument();
    expect(screen.getByText("SELL")).toBeInTheDocument();
    expect(screen.getByText("BUY")).toBeInTheDocument();
    expect(screen.getByText("$512.40")).toBeInTheDocument();
    expect(screen.getByText("$498.20")).toBeInTheDocument();
  });

  it("allows switching option types (Call, Put, Straddle)", async () => {
    render(<GammaScalperView initialSymbol="SPY" spotPrice={505.20} />);

    await screen.findByText(/Intraday Gamma Scalping/i);

    const straddleBtn = screen.getByRole("button", { name: "STRADDLE" });
    fireEvent.click(straddleBtn);

    const runBtn = screen.getByRole("button", { name: /Run Scalp Simulation/i });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(api.simulateGammaScalping).toHaveBeenCalledWith(
        expect.objectContaining({
          option_type: "STRADDLE",
          symbol: "SPY",
        })
      );
    });
  });

  it("allows modifying delta threshold and re-running simulation", async () => {
    render(<GammaScalperView initialSymbol="SPY" spotPrice={505.20} />);

    await screen.findByText(/Intraday Gamma Scalping/i);

    const thresholdSlider = screen.getByDisplayValue("0.15");
    fireEvent.change(thresholdSlider, { target: { value: "0.25" } });

    const runBtn = screen.getByRole("button", { name: /Run Scalp Simulation/i });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(api.simulateGammaScalping).toHaveBeenCalledWith(
        expect.objectContaining({
          delta_threshold: 0.25,
        })
      );
    });
  });

  it("calls onClose when close button clicked", async () => {
    const handleClose = vi.fn();
    render(<GammaScalperView initialSymbol="SPY" onClose={handleClose} />);

    const closeBtn = screen.getByText("✕ Close");
    fireEvent.click(closeBtn);

    expect(handleClose).toHaveBeenCalled();
  });
});

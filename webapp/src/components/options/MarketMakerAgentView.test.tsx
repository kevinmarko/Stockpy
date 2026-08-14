import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MarketMakerAgentView } from "./MarketMakerAgentView";
import { api } from "../../api/client";
import type { MarketMakerSimResponse } from "../../api/types";

vi.mock("../../api/client", () => ({
  api: {
    simulateMarketMakerAgent: vi.fn(),
  },
}));

const mockMmResponse: MarketMakerSimResponse = {
  symbol: "SPY",
  risk_aversion_gamma: 0.1,
  order_flow_intensity_kappa: 1.5,
  volatility_sigma: 0.2,
  max_inventory: 10,
  final_pnl: 142.5,
  sharpe_ratio: 2.84,
  max_drawdown: 12.4,
  total_trades: 28,
  fill_rate: 14.0,
  final_inventory: 2,
  avg_spread: 0.045,
  steps: [
    {
      step: 0,
      time_sec: 0,
      mid_price: 546.5,
      reservation_price: 546.5,
      bid_price: 546.47,
      ask_price: 546.53,
      bid_spread: 0.03,
      ask_spread: 0.03,
      inventory: 0,
      cash: 0,
      pnl: 0,
      trade_event: null,
    },
    {
      step: 1,
      time_sec: 234,
      mid_price: 546.6,
      reservation_price: 546.58,
      bid_price: 546.55,
      ask_price: 546.65,
      bid_spread: 0.05,
      ask_spread: 0.05,
      inventory: 1,
      cash: -546.55,
      pnl: 0.05,
      trade_event: "BUY",
    },
    {
      step: 2,
      time_sec: 468,
      mid_price: 546.7,
      reservation_price: 546.66,
      bid_price: 546.62,
      ask_price: 546.74,
      bid_spread: 0.08,
      ask_spread: 0.04,
      inventory: 2,
      cash: -1093.17,
      pnl: 142.5,
      trade_event: null,
    },
  ],
  as_of: new Date().toISOString(),
};

describe("MarketMakerAgentView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.simulateMarketMakerAgent).mockResolvedValue(mockMmResponse);
  });

  it("renders Avellaneda-Stoikov MM title, Phase 22 badge, and KPI metrics", async () => {
    render(<MarketMakerAgentView initialSymbol="SPY" spotPrice={546.5} />);

    expect(
      await screen.findByText(/Avellaneda-Stoikov High-Frequency Market Maker Agent/i)
    ).toBeInTheDocument();
    expect(screen.getByText("Phase 22")).toBeInTheDocument();
    expect(screen.getByText("+$142.50")).toBeInTheDocument();
    expect(screen.getByText("2.84")).toBeInTheDocument();
    expect(screen.getByText("14.0%")).toBeInTheDocument();
    expect(screen.getByText("$0.045")).toBeInTheDocument();
  });

  it("renders dynamic inventory exposure gauge with inventory level", async () => {
    render(<MarketMakerAgentView initialSymbol="SPY" spotPrice={546.5} />);

    expect(
      await screen.findByText(/Dynamic Inventory Exposure Gauge/i)
    ).toBeInTheDocument();
    expect(screen.getByText("+2 Long")).toBeInTheDocument();
    expect(screen.getByText("-10 Max Short")).toBeInTheDocument();
    expect(screen.getByText("+10 Max Long")).toBeInTheDocument();
  });

  it("renders parameter sliders for gamma, kappa, sigma, and max inventory", async () => {
    render(<MarketMakerAgentView initialSymbol="SPY" spotPrice={546.5} />);

    expect(await screen.findByText(/Risk Aversion \(γ\):/i)).toBeInTheDocument();
    expect(screen.getByText(/Flow Intensity \(κ\):/i)).toBeInTheDocument();
    expect(screen.getByText(/Asset Volatility \(σ\):/i)).toBeInTheDocument();
    expect(screen.getByText(/Max Inventory \(Q_max\):/i)).toBeInTheDocument();
  });

  it("triggers simulation when Run MM Agent Sim button is clicked", async () => {
    render(<MarketMakerAgentView initialSymbol="SPY" spotPrice={546.5} />);

    await screen.findByText("+$142.50");
    const runBtn = screen.getByRole("button", { name: /Run MM Agent Sim/i });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(api.simulateMarketMakerAgent).toHaveBeenCalledWith(
        expect.objectContaining({
          symbol: "SPY",
          risk_aversion_gamma: 0.1,
          order_flow_intensity_kappa: 1.5,
        })
      );
    });
  });
});

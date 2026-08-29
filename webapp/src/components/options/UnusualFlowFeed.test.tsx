import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { UnusualFlowFeed } from "./UnusualFlowFeed";
import { api } from "../../api/client";
import type { UnusualOptionsFlowResponse, FlowSentimentResponse } from "../../api/types";

vi.mock("../../api/client", () => ({
  api: {
    getUnusualOptionsFlow: vi.fn(),
    getOptionsFlowSentiment: vi.fn(),
  },
}));

const mockFlowResponse: UnusualOptionsFlowResponse = {
  trades: [
    {
      id: "uoa_1",
      symbol: "NVDA",
      timestamp: "14:48:12",
      option_type: "CALL",
      strike: 135.0,
      expiration: "2026-08-21",
      dte: 7,
      trade_type: "SWEEP",
      sentiment: "BULLISH",
      aggressor_side: "ASK",
      volume: 8420,
      open_interest: 1850,
      vol_oi_ratio: 4.55,
      price: 3.45,
      spot_price: 128.50,
      notional: 2904900,
      iv: 0.72,
      historical_vol_30d: 0.52,
      iv_expansion_flag: true,
    },
    {
      id: "uoa_2",
      symbol: "TSLA",
      timestamp: "14:45:30",
      option_type: "PUT",
      strike: 205.0,
      expiration: "2026-08-21",
      dte: 7,
      trade_type: "BLOCK",
      sentiment: "BEARISH",
      aggressor_side: "BID",
      volume: 6200,
      open_interest: 1400,
      vol_oi_ratio: 3.20,
      price: 4.10,
      spot_price: 218.00,
      notional: 2542000,
      iv: 0.76,
      historical_vol_30d: 0.58,
      iv_expansion_flag: true,
    },
  ],
  count: 2,
  as_of: "2026-08-14T14:48:00Z",
};

const mockSentimentResponse: FlowSentimentResponse = {
  sentiment: {
    symbol: "NVDA",
    sentiment_score: 0.72,
    bullish_notional: 4200000,
    bearish_notional: 680000,
    total_notional: 4880000,
    call_volume: 24500,
    put_volume: 5800,
    put_call_ratio: 0.24,
    top_active_strikes: [
      { strike: 135.0, option_type: "CALL", notional: 2904900 },
      { strike: 140.0, option_type: "CALL", notional: 1295100 },
    ],
  },
  as_of: "2026-08-14T14:48:00Z",
};

describe("UnusualFlowFeed", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getUnusualOptionsFlow).mockResolvedValue(mockFlowResponse);
    vi.mocked(api.getOptionsFlowSentiment).mockResolvedValue(mockSentimentResponse);
  });

  it("renders flow feed header, sentiment gauge, and sweep trades", async () => {
    render(<UnusualFlowFeed />);

    expect(await screen.findByText(/Unusual Options Activity & Order Flow Feed/i)).toBeInTheDocument();
    expect(screen.getByText(/Institutional Net Flow Sentiment/i)).toBeInTheDocument();
    expect(screen.getByText(/BULLISH \(72%\)/i)).toBeInTheDocument();
    expect(await screen.findAllByText("NVDA")).toHaveLength(2); // 1 in sentiment header, 1 in table row
    expect(screen.getByText("TSLA")).toBeInTheDocument();
    expect(screen.getByText("4.55x")).toBeInTheDocument();
    expect(screen.getByText("⚡ SWEEP")).toBeInTheDocument();
  });

  it("filters trades by sentiment (Bullish / Bearish pills)", async () => {
    render(<UnusualFlowFeed />);

    await screen.findAllByText("NVDA");
    const bearishBtn = screen.getByRole("button", { name: /🔴 Bearish/i });
    fireEvent.click(bearishBtn);

    expect(screen.getByText("TSLA")).toBeInTheDocument();
    // NVDA should only be in sentiment header, not in the table
    expect(screen.getAllByText("NVDA")).toHaveLength(1);

    const bullishBtn = screen.getByRole("button", { name: /🟢 Bullish/i });
    fireEvent.click(bullishBtn);

    expect(screen.getAllByText("NVDA")).toHaveLength(2);
    expect(screen.queryByText("TSLA")).not.toBeInTheDocument();
  });

  it("filters trades by trade type (Sweeps vs Blocks)", async () => {
    render(<UnusualFlowFeed />);

    await screen.findAllByText("NVDA");
    const sweepBtn = screen.getByRole("button", { name: /⚡ Sweeps/i });
    fireEvent.click(sweepBtn);

    expect(screen.getAllByText("NVDA")).toHaveLength(2);
    expect(screen.queryByText("TSLA")).not.toBeInTheDocument();

    const blockBtn = screen.getByRole("button", { name: /🏢 Blocks/i });
    fireEvent.click(blockBtn);

    expect(screen.getByText("TSLA")).toBeInTheDocument();
    expect(screen.getAllByText("NVDA")).toHaveLength(1);
  });

  it("allows searching symbol and fetching symbol-specific sentiment", async () => {
    render(<UnusualFlowFeed initialSymbol="TSLA" />);

    await waitFor(() => {
      expect(api.getOptionsFlowSentiment).toHaveBeenCalledWith("TSLA");
    });
  });

  it("triggers onSelectTicker when trade row clicked", async () => {
    const handleSelect = vi.fn();
    render(<UnusualFlowFeed onSelectTicker={handleSelect} />);

    const nvdaElements = await screen.findAllByText("NVDA");
    // Click the one in the table row (the second one)
    fireEvent.click(nvdaElements[1]);

    expect(handleSelect).toHaveBeenCalledWith("NVDA");
  });

  it("calls onClose when close button clicked", async () => {
    const handleClose = vi.fn();
    render(<UnusualFlowFeed onClose={handleClose} />);

    const closeBtn = await screen.findByText("✕ Close");
    fireEvent.click(closeBtn);

    expect(handleClose).toHaveBeenCalled();
  });

  it("renders correctly when backend returns records format without trades key", async () => {
    vi.mocked(api.getUnusualOptionsFlow).mockResolvedValue({
      records: [
        {
          contract_symbol: "AAPL260821C00230000",
          symbol: "AAPL",
          timestamp: "15:01:00",
          option_type: "call",
          strike: 230.0,
          expiration: "2026-08-21",
          aggressiveness: "ask_sweep",
          sentiment: "bullish",
          volume: 5000,
          open_interest: 800,
          vol_oi_ratio: 6.25,
          trade_price: 5.5,
          underlying_notional: 2750000,
          iv: 0.35,
          price: 5.5,
          notional: 2750000,
        },
      ],
      count: 1,
    } as any);

    render(<UnusualFlowFeed />);

    expect(await screen.findByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("6.25x")).toBeInTheDocument();
    expect(screen.getByText("⚡ SWEEP")).toBeInTheDocument();
    expect(screen.getByText("🟢 BULLISH")).toBeInTheDocument();
  });

  it("shows an incomplete-scan banner when the backend reports degraded coverage", async () => {
    vi.mocked(api.getUnusualOptionsFlow).mockResolvedValue({
      ...mockFlowResponse,
      degraded: true,
      symbols_fetch_failed: ["TSLA"],
    });

    render(<UnusualFlowFeed />);

    expect(await screen.findByText(/Incomplete Scan/i)).toBeInTheDocument();
    expect(screen.getByText(/Live options-chain data failed to load for TSLA/i)).toBeInTheDocument();
  });

  it("does not show the incomplete-scan banner on a healthy (non-degraded) response", async () => {
    render(<UnusualFlowFeed />);

    await screen.findByText(/Unusual Options Activity & Order Flow Feed/i);
    expect(screen.queryByText(/Incomplete Scan/i)).not.toBeInTheDocument();
  });

  it("marks an estimated price/spot price honestly, never rendering it identically to a real quote", async () => {
    vi.mocked(api.getUnusualOptionsFlow).mockResolvedValue({
      trades: [
        {
          ...mockFlowResponse.trades[0],
          price_is_estimated: true,
          spot_price_is_estimated: true,
        },
      ],
      count: 1,
      as_of: mockFlowResponse.as_of,
    });

    render(<UnusualFlowFeed />);

    await screen.findAllByText("NVDA");
    // Two "(est.)" badges: one next to spot price, one next to fill price
    // (notional carries a third, since it's derived from the estimated price).
    expect(screen.getAllByText("(est.)").length).toBeGreaterThanOrEqual(2);
  });
});

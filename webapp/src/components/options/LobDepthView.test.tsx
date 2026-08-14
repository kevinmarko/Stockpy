import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { LobDepthView } from "./LobDepthView";
import { api } from "../../api/client";
import type { LobQueueSimulationResponse } from "../../api/types";

vi.mock("../../api/client", () => ({
  api: {
    simulateLobQueue: vi.fn(),
  },
}));

const mockLobResponse: LobQueueSimulationResponse = {
  symbol: "SPY",
  strike: 540,
  option_type: "CALL",
  limit_price: 3.15,
  order_size: 5,
  order_side: "BUY",
  queue_priority_position: 3,
  orders_ahead: 2,
  size_ahead: 28,
  fill_probability_30s: 0.824,
  fill_probability_60s: 0.904,
  fill_probability_300s: 0.965,
  estimated_fill_time_seconds: 14.5,
  fill_time_p50: 12.3,
  fill_time_p95: 23.8,
  bids: [
    {
      price: 3.15,
      size: 45,
      num_orders: 4,
      is_user_level: true,
      user_queue_position: 3,
    },
    {
      price: 3.14,
      size: 75,
      num_orders: 5,
      is_user_level: false,
    },
  ],
  asks: [
    {
      price: 3.17,
      size: 50,
      num_orders: 4,
      is_user_level: false,
    },
    {
      price: 3.18,
      size: 75,
      num_orders: 6,
      is_user_level: false,
    },
  ],
  spread: 0.02,
  mid_price: 3.16,
  market_depth_summary: "Queue Priority #3 at $3.15 (2 orders / 28 contracts ahead). Expected fill latency: ~14.5s (30s P(Fill) = 82.4%).",
  as_of: new Date().toISOString(),
};

describe("LobDepthView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.simulateLobQueue).mockResolvedValue(mockLobResponse);
  });

  it("renders LOB desk title, Phase 21 badge, Queue Priority, and fill latency", async () => {
    render(<LobDepthView initialSymbol="SPY" spotPrice={546.50} />);

    expect(
      await screen.findByText(/Level-3 Limit Order Book \(LOB\) Depth & Queue Position Simulator/i)
    ).toBeInTheDocument();
    expect(screen.getByText("Phase 21")).toBeInTheDocument();
    expect(screen.getByText("#3 in Line")).toBeInTheDocument();
    expect(screen.getByText("~14.5s")).toBeInTheDocument();
  });

  it("renders 30s fill probability gauge and depth spread metrics", async () => {
    render(<LobDepthView initialSymbol="SPY" spotPrice={546.50} />);

    expect(await screen.findByText("82%")).toBeInTheDocument();
    expect(screen.getByText("60s: 90%")).toBeInTheDocument();
    expect(screen.getByText("300s: 97%")).toBeInTheDocument();
    expect(screen.getByText("$0.02 Spread")).toBeInTheDocument();
    expect(screen.getByText("Mid Price: $3.16")).toBeInTheDocument();
  });

  it("renders Level-3 Bid and Ask depth ladder with user queue indicator", async () => {
    render(<LobDepthView initialSymbol="SPY" spotPrice={546.50} />);

    expect(
      await screen.findByText(/SPY \$540 CALL — Level-3 LOB Depth Ladder/i)
    ).toBeInTheDocument();
    expect(screen.getByText("★ You (#3)")).toBeInTheDocument();
    expect(screen.getByText("$3.15")).toBeInTheDocument();
    expect(screen.getByText("$3.17")).toBeInTheDocument();
  });

  it("triggers simulation when parameters changed and simulate button clicked", async () => {
    render(<LobDepthView initialSymbol="SPY" spotPrice={546.50} />);

    await screen.findByText("#3 in Line");

    const putBtn = screen.getByRole("button", { name: "PUT" });
    fireEvent.click(putBtn);

    const simBtn = screen.getByRole("button", { name: /⚡ Simulate Fill/i });
    fireEvent.click(simBtn);

    await waitFor(() => {
      expect(api.simulateLobQueue).toHaveBeenCalledWith(
        expect.objectContaining({
          option_type: "PUT",
          symbol: "SPY",
        })
      );
    });
  });

  it("switches ticker when ticker pill clicked", async () => {
    render(<LobDepthView initialSymbol="SPY" spotPrice={546.50} />);

    await screen.findByText("#3 in Line");
    const qqqBtn = screen.getByRole("button", { name: "QQQ" });
    fireEvent.click(qqqBtn);

    await waitFor(() => {
      expect(api.simulateLobQueue).toHaveBeenCalledWith(
        expect.objectContaining({
          symbol: "QQQ",
        })
      );
    });
  });

  it("calls onClose when close button clicked", async () => {
    const handleClose = vi.fn();
    render(<LobDepthView onClose={handleClose} />);

    const closeBtn = await screen.findByText("✕ Close");
    fireEvent.click(closeBtn);

    expect(handleClose).toHaveBeenCalled();
  });
});

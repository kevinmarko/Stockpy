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
  valid: true,
  symbol: "SPY",
  price_level: 3.15,
  order_size: 5,
  depth_ahead: 28,
  time_horizon_sec: 60,
  num_simulations: 500,
  fill_probability: 0.824,
  expected_fill_time_sec: 14.5,
  expected_wait_time_sec: 14.5,
  unconditional_fill_time_sec: 12.0,
  median_fill_time_sec: 12.3,
  prob_adverse_move_before_fill: 0.18,
  expected_fill_ratio: 0.91,
  queue_depletion_velocity: 0.42,
  queue_progression_percentiles: {
    p10: 5.1,
    p25: 8.7,
    p50: 12.3,
    p75: 17.4,
    p90: 23.2,
    p95: 27.6,
  },
  cst_closed_form_fill_prob: 0.82,
  reason: null,
  timestamp: new Date().toISOString(),
  as_of: new Date().toISOString(),
};

describe("LobDepthView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.simulateLobQueue).mockResolvedValue(mockLobResponse);
  });

  it("renders LOB desk title, Phase 21 badge, and fill probability", async () => {
    render(<LobDepthView initialSymbol="SPY" spotPrice={546.5} />);

    expect(
      await screen.findByText(/Level-3 Limit Order Book \(LOB\) Depth & Queue Position Simulator/i)
    ).toBeInTheDocument();
    expect(screen.getByText("Phase 21")).toBeInTheDocument();
    expect(screen.getByText("82%")).toBeInTheDocument();
    expect(screen.getByText("~14.5s")).toBeInTheDocument();
  });

  it("renders queue depletion velocity, adverse-move risk, and expected fill ratio", async () => {
    render(<LobDepthView initialSymbol="SPY" spotPrice={546.5} />);

    await screen.findByText("82%");
    expect(screen.getByText("0.420 contracts/s")).toBeInTheDocument();
    expect(screen.getByText("P(Adverse Move Before Fill): 18%")).toBeInTheDocument();
    expect(screen.getByText("91.0%")).toBeInTheDocument();
  });

  it("renders queue position bar and time-to-fill percentile table", async () => {
    render(<LobDepthView initialSymbol="SPY" spotPrice={546.5} />);

    expect(await screen.findByText(/SPY @ \$3\.15 — Queue Position/i)).toBeInTheDocument();
    expect(screen.getByText("★ You (5)")).toBeInTheDocument();
    expect(screen.getByText("28 ahead")).toBeInTheDocument();
    expect(screen.getByText("P50")).toBeInTheDocument();
    expect(screen.getByText("12.3s")).toBeInTheDocument();
  });

  it("triggers simulation with price_level/order_size/depth_ahead when simulate button clicked", async () => {
    render(<LobDepthView initialSymbol="SPY" spotPrice={546.5} />);

    await screen.findByText("82%");

    const simBtn = screen.getByRole("button", { name: /⚡ Simulate Fill/i });
    fireEvent.click(simBtn);

    await waitFor(() => {
      expect(api.simulateLobQueue).toHaveBeenCalledWith(
        expect.objectContaining({
          symbol: "SPY",
          price_level: 3.15,
          order_size: 5,
          depth_ahead: 28,
        })
      );
    });
  });

  it("switches ticker when ticker pill clicked", async () => {
    render(<LobDepthView initialSymbol="SPY" spotPrice={546.5} />);

    await screen.findByText("82%");
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

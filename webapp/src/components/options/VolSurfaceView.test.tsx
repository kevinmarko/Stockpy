import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { VolSurfaceView } from "./VolSurfaceView";
import { api } from "../../api/client";
import type { VolSurfaceResponse } from "../../api/types";

vi.mock("../../api/client", () => ({
  api: {
    getVolSurface: vi.fn(),
  },
}));

const mockVolData: VolSurfaceResponse = {
  symbol: "SPY",
  spot_price: 505.20,
  as_of: "2026-08-14T14:00:00Z",
  expirations: ["2026-08-21", "2026-09-18", "2026-10-16"],
  selected_expiration: "2026-09-18",
  smile_points: [
    { strike: 480, iv: 0.265, moneyness: 0.95 },
    { strike: 505, iv: 0.215, moneyness: 1.0 },
    { strike: 530, iv: 0.195, moneyness: 1.05 },
  ],
  term_structure: [
    { expiration: "2026-08-21", dte: 7, atm_iv: 0.185, historical_realized_vol_30d: 0.165 },
    { expiration: "2026-09-18", dte: 35, atm_iv: 0.215, historical_realized_vol_30d: 0.165 },
    { expiration: "2026-10-16", dte: 63, atm_iv: 0.228, historical_realized_vol_30d: 0.165 },
  ],
  skew: {
    skew_25delta: 0.035,
    put_25delta_iv: 0.252,
    call_25delta_iv: 0.217,
    atm_iv: 0.215,
    vrp_spread: 0.050,
    realized_vol_10d: 0.152,
    realized_vol_20d: 0.160,
    realized_vol_30d: 0.165,
    realized_vol_60d: 0.172,
  },
};

describe("VolSurfaceView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getVolSurface).mockResolvedValue(mockVolData);
  });

  it("renders volatility surface title, spot price, and skew metrics", async () => {
    render(<VolSurfaceView initialSymbol="SPY" />);

    expect(await screen.findByText(/Volatility Surface & Skew Analytics/i)).toBeInTheDocument();
    expect(screen.getByText("SPY $505.20")).toBeInTheDocument();
    expect(screen.getByText("+3.50%")).toBeInTheDocument(); // 25Δ skew
    expect(screen.getByText("+5.00%")).toBeInTheDocument(); // VRP spread
    expect(screen.getByText("21.50%")).toBeInTheDocument(); // ATM IV
  });

  it("renders term structure expiries and allows switching expiration", async () => {
    render(<VolSurfaceView initialSymbol="SPY" />);

    const exp1 = await screen.findByRole("button", { name: /2026-08-21/i });
    expect(exp1).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /2026-09-18/i })).toBeInTheDocument();

    const expBtn = screen.getByRole("button", { name: /2026-10-16/i });
    fireEvent.click(expBtn);

    await waitFor(() => {
      expect(api.getVolSurface).toHaveBeenCalledWith("SPY", "2026-10-16");
    });
  });

  it("allows changing ticker symbol", async () => {
    render(<VolSurfaceView initialSymbol="SPY" />);

    const input = screen.getByPlaceholderText("Ticker...");
    fireEvent.change(input, { target: { value: "QQQ" } });

    const submitBtn = screen.getByText("Go");
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(api.getVolSurface).toHaveBeenCalledWith("QQQ", undefined);
    });
  });

  it("calls onClose when close button clicked", async () => {
    const handleClose = vi.fn();
    render(<VolSurfaceView initialSymbol="SPY" onClose={handleClose} />);

    const closeBtn = screen.getByText("✕ Close");
    fireEvent.click(closeBtn);

    expect(handleClose).toHaveBeenCalled();
  });
});

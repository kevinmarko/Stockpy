import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { CopulaSpreadView } from "./CopulaSpreadView";
import { api } from "../../api/client";
import type { CopulaPairsResponse } from "../../api/types";

vi.mock("../../api/client", () => ({
  api: {
    getCopulaPairsAnalysis: vi.fn(),
  },
}));

const mockCopulaResponse: CopulaPairsResponse = {
  pair: "SPY/QQQ",
  asset_x: "SPY",
  asset_y: "QQQ",
  copula_family: "Clayton",
  tail_dependence: {
    lower_tail_dependence: 0.725,
    upper_tail_dependence: 0.0,
    copula_family: "Clayton",
    theta: 2.15,
    log_likelihood: 178.4,
    aic: -352.8,
    kendall_tau: 0.518,
  },
  kalman_beta: 1.234,
  kalman_alpha: -12.4,
  ou_half_life_days: 14.2,
  spread_z_score: 2.18,
  current_spread: 8.45,
  signal_action: "SHORT_SPREAD",
  historical_series: [
    {
      date: "2026-07-01",
      asset_x_price: 540.2,
      asset_y_price: 654.1,
      kalman_beta: 1.21,
      spread: 2.1,
      spread_z_score: 0.5,
      upper_band_2sigma: 2.0,
      lower_band_2sigma: -2.0,
    },
    {
      date: "2026-07-15",
      asset_x_price: 548.5,
      asset_y_price: 672.3,
      kalman_beta: 1.234,
      spread: 8.45,
      spread_z_score: 2.18,
      upper_band_2sigma: 2.0,
      lower_band_2sigma: -2.0,
    },
  ],
  as_of: new Date().toISOString(),
  status_note: "Fitted Clayton Copula on SPY/QQQ with dynamic Kalman beta.",
};

describe("CopulaSpreadView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getCopulaPairsAnalysis).mockResolvedValue(mockCopulaResponse);
  });

  it("renders Copula Stat Arb title, Phase 21 badge, and signal action", async () => {
    render(<CopulaSpreadView initialPair="SPY/QQQ" />);

    expect(
      await screen.findByText(/Copula Statistical Arbitrage & Dynamic Kalman Beta/i)
    ).toBeInTheDocument();
    expect(screen.getByText("Phase 21")).toBeInTheDocument();
    expect(screen.getByText("SHORT SPREAD")).toBeInTheDocument();
  });

  it("renders key metrics: Kalman dynamic beta, Spread Z-Score, and OU Half-Life", async () => {
    render(<CopulaSpreadView initialPair="SPY/QQQ" />);

    expect(await screen.findByText("1.234")).toBeInTheDocument();
    expect(screen.getAllByText("+2.18σ").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("14.2")).toBeInTheDocument();
    expect(screen.getByText("Clayton")).toBeInTheDocument();
  });

  it("renders Lower and Upper Tail Dependence gauges", async () => {
    render(<CopulaSpreadView initialPair="SPY/QQQ" />);

    const tailElements = await screen.findAllByText(/Lower Tail Crisis Dependence/i);
    expect(tailElements.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("72.5%")).toBeInTheDocument();
    expect(
      screen.getByText(/Upper Tail Momentum Dependence/i)
    ).toBeInTheDocument();
    expect(screen.getByText("0.0%")).toBeInTheDocument();
  });

  it("allows selecting preset pairs and loads new data", async () => {
    render(<CopulaSpreadView initialPair="SPY/QQQ" />);

    await screen.findByText("1.234");
    const nvdaBtn = screen.getByRole("button", { name: /NVDA \/ AMD/i });
    fireEvent.click(nvdaBtn);

    await waitFor(() => {
      expect(api.getCopulaPairsAnalysis).toHaveBeenCalledWith("NVDA/AMD");
    });
  });

  it("renders action directive with specific leg execution instructions", async () => {
    render(<CopulaSpreadView initialPair="SPY/QQQ" />);

    expect(
      await screen.findByText(/Signal Directive: SHORT SPREAD/i)
    ).toBeInTheDocument();
    expect(screen.getByText("SELL 1.0 QQQ")).toBeInTheDocument();
    expect(screen.getByText("BUY 1.234 SPY")).toBeInTheDocument();
  });
});

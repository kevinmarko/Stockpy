import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { VolForecastScanner } from "./VolForecastScanner";
import { api } from "../../api/client";
import type { HarRvForecastResponse, VolMispricingResponse, OptionsAlertTestResult } from "../../api/types";

vi.mock("../../api/client", () => ({
  api: {
    getHarRvForecast: vi.fn(),
    getVolMispricing: vi.fn(),
    testOptionsAlert: vi.fn(),
  },
}));

const mockForecast: HarRvForecastResponse = {
  symbol: "SPY",
  spot_price: 505.20,
  as_of: "2026-08-14T14:00:00Z",
  rv_daily: 0.158,
  rv_weekly: 0.168,
  rv_monthly: 0.178,
  forecast_vol_1d: 0.165,
  forecast_vol_5d: 0.170,
  forecast_vol_22d: 0.175,
  forecast_vol_30d: 0.180,
  gjr_garch_vol: 0.185,
  fair_iv_blend: 0.182,
  coefficients: {
    beta_0: 0.015,
    beta_d: 0.38,
    beta_w: 0.34,
    beta_m: 0.22,
  },
  r_squared: 0.685,
};

const mockMispricing: VolMispricingResponse = {
  symbol: "SPY",
  spot_price: 505.20,
  expiration: "2026-09-18",
  expirations: ["2026-08-21", "2026-09-18", "2026-10-16"],
  dte: 35,
  fair_iv_baseline: 0.182,
  market_atm_iv: 0.215,
  rich_strikes_count: 2,
  cheap_strikes_count: 1,
  strikes: [
    {
      strike: 480,
      option_type: "PUT",
      market_iv: 0.265,
      fair_iv: 0.210,
      iv_spread: 0.055,
      spread_zscore: 2.2,
      classification: "RICH",
      suggested_action: "SELL_PREMIUM",
      bid: 4.20,
      ask: 4.50,
      delta: -0.22,
    },
    {
      strike: 505,
      option_type: "CALL",
      market_iv: 0.215,
      fair_iv: 0.212,
      iv_spread: 0.003,
      spread_zscore: 0.12,
      classification: "FAIR",
      suggested_action: "HOLD",
      bid: 8.10,
      ask: 8.40,
      delta: 0.50,
    },
    {
      strike: 530,
      option_type: "CALL",
      market_iv: 0.155,
      fair_iv: 0.180,
      iv_spread: -0.025,
      spread_zscore: -1.2,
      classification: "CHEAP",
      suggested_action: "BUY_GAMMA",
      bid: 1.20,
      ask: 1.40,
      delta: 0.18,
    },
  ],
  trade_recommendations: [
    {
      strategy: "Put Credit Spread (Rich Skew Capture)",
      direction: "SELL_VOL",
      strikes: [480, 470],
      reason: "OTM Puts trade at elevated IV premium over HAR-RV fair value.",
      estimated_edge_pct: 18.5,
    },
  ],
  as_of: "2026-08-14T14:00:00Z",
};

const mockAlertResult: OptionsAlertTestResult = {
  ok: true,
  dispatched_count: 3,
  channels: ["Discord Webhook (#options-flow)", "Slack Webhook (#trading-desk)"],
  results: [
    {
      channel: "Discord Webhook (#options-flow)",
      status: "SENT",
      message: "Dispatched test UOA notification.",
    },
  ],
  as_of: "2026-08-14T14:00:00Z",
};

describe("VolForecastScanner", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getHarRvForecast).mockResolvedValue(mockForecast);
    vi.mocked(api.getVolMispricing).mockResolvedValue(mockMispricing);
    vi.mocked(api.testOptionsAlert).mockResolvedValue(mockAlertResult);
  });

  it("renders scanner title, spot price, HAR-RV equation, and KPI cards", async () => {
    render(<VolForecastScanner initialSymbol="SPY" />);

    expect(await screen.findByText(/HAR-RV Volatility Forecaster & Mispricing Scanner/i)).toBeInTheDocument();
    expect(screen.getByText("SPY $505.20")).toBeInTheDocument();
    expect(screen.getByText("18.20%")).toBeInTheDocument(); // Fair IV Blend
    expect(screen.getByText("2 Strikes")).toBeInTheDocument(); // Rich strikes
    expect(screen.getByText("1 Strikes")).toBeInTheDocument(); // Cheap strikes
    expect(screen.getByText(/Corsi \(2009\) Heterogeneous Autoregressive/i)).toBeInTheDocument();
  });

  it("renders strike mispricing ledger and filters by rich/cheap classification", async () => {
    render(<VolForecastScanner initialSymbol="SPY" />);

    const strike480 = await screen.findAllByText("$480");
    expect(strike480.length).toBeGreaterThan(0);
    expect(screen.getAllByText("$505").length).toBeGreaterThan(0);
    expect(screen.getAllByText("$530").length).toBeGreaterThan(0);

    // Click "Rich (Sell)" filter button
    const richFilterBtn = screen.getByRole("button", { name: /Rich \(Sell\)/i });
    fireEvent.click(richFilterBtn);

    expect(screen.getAllByText("$480").length).toBeGreaterThan(0);
    expect(screen.queryByText("HOLD")).not.toBeInTheDocument();

    // Click "Cheap (Buy)" filter button
    const cheapFilterBtn = screen.getByRole("button", { name: /Cheap \(Buy\)/i });
    fireEvent.click(cheapFilterBtn);

    expect(screen.getAllByText("$530").length).toBeGreaterThan(0);
    expect(screen.queryByText("SELL_PREMIUM")).not.toBeInTheDocument();
  });

  it("allows switching expirations", async () => {
    render(<VolForecastScanner initialSymbol="SPY" />);

    const expBtn = await screen.findByRole("button", { name: /2026-10-16/i });
    fireEvent.click(expBtn);

    await waitFor(() => {
      expect(api.getVolMispricing).toHaveBeenCalledWith("SPY", "2026-10-16");
    });
  });

  it("allows changing ticker symbol", async () => {
    render(<VolForecastScanner initialSymbol="SPY" />);

    const input = screen.getByPlaceholderText("Ticker...");
    fireEvent.change(input, { target: { value: "NVDA" } });

    const submitBtn = screen.getByText("Go");
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(api.getHarRvForecast).toHaveBeenCalledWith("NVDA");
      expect(api.getVolMispricing).toHaveBeenCalledWith("NVDA", undefined);
    });
  });

  it("dispatches test options alerts", async () => {
    render(<VolForecastScanner initialSymbol="SPY" />);

    const uoaAlertBtn = await screen.findByRole("button", { name: /UOA Whale Sweep/i });
    fireEvent.click(uoaAlertBtn);

    await waitFor(() => {
      expect(api.testOptionsAlert).toHaveBeenCalledWith({ alert_type: "UOA", symbol: "SPY" });
      expect(screen.getByText(/Dispatched to Discord Webhook/i)).toBeInTheDocument();
    });
  });

  it("calls onClose when close button clicked", async () => {
    const handleClose = vi.fn();
    render(<VolForecastScanner initialSymbol="SPY" onClose={handleClose} />);

    const closeBtn = screen.getByText("✕ Close");
    fireEvent.click(closeBtn);

    expect(handleClose).toHaveBeenCalled();
  });
});

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { GexProfileView } from "./GexProfileView";
import { api } from "../../api/client";
import type { GexProfileResponse } from "../../api/types";

vi.mock("../../api/client", () => ({
  api: {
    getOptionsGexProfile: vi.fn(),
  },
}));

// net_gex/call_gex/put_gex are raw DOLLAR figures on the real backend (not
// pre-scaled to millions) -- values below are the "millions" magnitude from
// the old fixture, scaled by 1e6, matching GexProfileView's own /1e6 display.
const mockGexResponse: GexProfileResponse = {
  symbol: "SPY",
  spot_price: 546.50,
  net_gex: 1245.80 * 1e6,
  total_call_gex: 965.7 * 1e6,
  total_put_gex: -705.2 * 1e6,
  zero_gamma_flip: 538.30,
  call_wall_strike: 555.00,
  put_wall_strike: 530.00,
  gamma_regime: "POSITIVE_GAMMA",
  regime_description: "Positive Gamma Regime ($1245.8M Net GEX). Market makers long gamma; intraday mean-reversion dampens realized volatility.",
  dealer_hedging_flow: 12458000,
  strikes: [
    {
      strike: 530.00,
      call_gex: 45.2 * 1e6,
      put_gex: -380.0 * 1e6,
      net_gex: -334.8 * 1e6,
      call_oi: 3200,
      put_oi: 14500,
      gamma_concentration_pct: 12.5,
    },
    {
      strike: 538.30,
      call_gex: 180.0 * 1e6,
      put_gex: -180.0 * 1e6,
      net_gex: 0.0,
      call_oi: 8500,
      put_oi: 8500,
      gamma_concentration_pct: 15.0,
    },
    {
      strike: 546.50,
      call_gex: 320.5 * 1e6,
      put_gex: -110.2 * 1e6,
      net_gex: 210.3 * 1e6,
      call_oi: 12000,
      put_oi: 4800,
      gamma_concentration_pct: 18.2,
    },
    {
      strike: 555.00,
      call_gex: 420.0 * 1e6,
      put_gex: -35.0 * 1e6,
      net_gex: 385.0 * 1e6,
      call_oi: 15400,
      put_oi: 1200,
      gamma_concentration_pct: 21.4,
    },
  ],
  as_of: new Date().toISOString(),
  spot_price_source: "live",
  chain_source: "live",
};

describe("GexProfileView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getOptionsGexProfile).mockResolvedValue(mockGexResponse);
  });

  it("renders GEX desk title, Phase 20 badge, spot price, and Net GEX KPI", async () => {
    render(<GexProfileView initialSymbol="SPY" spotPrice={546.50} />);

    expect(
      await screen.findByText(/Options Gamma Exposure \(GEX\) & Dealer Hedging Desk/i)
    ).toBeInTheDocument();
    expect(screen.getByText("Phase 20")).toBeInTheDocument();
    expect(screen.getByText("+$1245.8M")).toBeInTheDocument();
  });

  it("renders Volatility Regime indicator, Zero-Gamma Flip level, and Gamma Walls", async () => {
    render(<GexProfileView initialSymbol="SPY" spotPrice={546.50} />);

    expect(await screen.findByText(/🛡️ Vol Dampener/i)).toBeInTheDocument();
    expect(screen.getByText("POSITIVE GAMMA")).toBeInTheDocument();
    expect(screen.getByText("$538.30")).toBeInTheDocument(); // Zero flip
    expect(screen.getByText(/📈 Call Wall: \$555.00/i)).toBeInTheDocument();
    expect(screen.getByText(/📉 Put Wall: \$530.00/i)).toBeInTheDocument();
  });

  it("allows switching between GEX Chart and Strike Ladder table", async () => {
    render(<GexProfileView initialSymbol="SPY" spotPrice={546.50} />);

    await screen.findByText("+$1245.8M");
    const tableBtn = screen.getByRole("button", { name: "📋 Strike Ladder" });
    fireEvent.click(tableBtn);

    expect(screen.getByText("SPY GEX Strike Exposure Ladder")).toBeInTheDocument();
    expect(screen.getByText("CALL WALL")).toBeInTheDocument();
    expect(screen.getByText("PUT WALL")).toBeInTheDocument();
  });

  it("switches ticker when ticker pill clicked", async () => {
    render(<GexProfileView initialSymbol="SPY" spotPrice={546.50} />);

    await screen.findByText("+$1245.8M");
    const qqqBtn = screen.getByRole("button", { name: "QQQ" });
    fireEvent.click(qqqBtn);

    await waitFor(() => {
      expect(api.getOptionsGexProfile).toHaveBeenCalledWith("QQQ");
    });
  });

  it("triggers onSelectStrike callback when strike clicked", async () => {
    const handleSelectStrike = vi.fn();
    render(
      <GexProfileView
        initialSymbol="SPY"
        spotPrice={546.50}
        onSelectStrike={handleSelectStrike}
      />
    );

    await screen.findByText("+$1245.8M");
    const strikeElement = screen.getByText("$555");
    fireEvent.click(strikeElement);

    expect(handleSelectStrike).toHaveBeenCalledWith(555);
  });

  it("calls onClose when close button clicked", async () => {
    const handleClose = vi.fn();
    render(<GexProfileView onClose={handleClose} />);

    const closeBtn = await screen.findByText("✕ Close");
    fireEvent.click(closeBtn);

    expect(handleClose).toHaveBeenCalled();
  });

  it("does not render a synthetic-data banner for a live chain_source", async () => {
    render(<GexProfileView initialSymbol="SPY" spotPrice={546.50} />);

    await screen.findByText("+$1245.8M");
    expect(screen.queryByText(/Synthetic Data/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Demo Data/i)).not.toBeInTheDocument();
  });

  it("renders an honest synthetic-data banner when the backend fell back to a generated chain (CONSTRAINT #4)", async () => {
    // Regression for the confirmed audit finding (2026-08-24): the API
    // already flags chain_source/spot_price_source honestly on a live-chain
    // resolution failure, but this component previously never read either
    // field, so a fully fabricated GEX profile rendered indistinguishably
    // from a real one.
    vi.mocked(api.getOptionsGexProfile).mockResolvedValue({
      ...mockGexResponse,
      chain_source: "synthetic",
      spot_price_source: "unavailable",
    });

    render(<GexProfileView initialSymbol="ZZZZ" spotPrice={546.50} />);

    expect(await screen.findByText(/Synthetic Data/i)).toBeInTheDocument();
    expect(
      screen.getByText(/No live options chain or spot quote could be resolved/i)
    ).toBeInTheDocument();
  });

  it("renders a Demo Data banner when running against the offline mock backend", async () => {
    vi.mocked(api.getOptionsGexProfile).mockResolvedValue({
      ...mockGexResponse,
      chain_source: "mock",
      spot_price_source: "mock",
    });

    render(<GexProfileView initialSymbol="SPY" spotPrice={546.50} />);

    expect(await screen.findByText(/Demo Data/i)).toBeInTheDocument();
  });
});

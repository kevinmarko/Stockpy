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

const mockGexResponse: GexProfileResponse = {
  symbol: "SPY",
  spot_price: 546.50,
  net_gex_dollars: 1245.80,
  zero_gamma_flip: 538.30,
  call_gamma_wall: 555.00,
  put_gamma_wall: 530.00,
  volatility_regime: "VOL_DAMPENER",
  strikes: [
    {
      strike: 530.00,
      call_gex: 45.2,
      put_gex: -380.0,
      net_gex: -334.8,
      open_interest_calls: 3200,
      open_interest_puts: 14500,
      gamma_calls: 0.012,
      gamma_puts: 0.045,
    },
    {
      strike: 538.30,
      call_gex: 180.0,
      put_gex: -180.0,
      net_gex: 0.0,
      open_interest_calls: 8500,
      open_interest_puts: 8500,
      gamma_calls: 0.035,
      gamma_puts: 0.035,
    },
    {
      strike: 546.50,
      call_gex: 320.5,
      put_gex: -110.2,
      net_gex: 210.3,
      open_interest_calls: 12000,
      open_interest_puts: 4800,
      gamma_calls: 0.052,
      gamma_puts: 0.021,
    },
    {
      strike: 555.00,
      call_gex: 420.0,
      put_gex: -35.0,
      net_gex: 385.0,
      open_interest_calls: 15400,
      open_interest_puts: 1200,
      gamma_calls: 0.048,
      gamma_puts: 0.008,
    },
  ],
  as_of: new Date().toISOString(),
  dealer_positioning_bias: "Positive Gamma Regime ($1245.8M Net GEX). Market makers long gamma; intraday mean-reversion dampens realized volatility.",
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
    expect(screen.getByText("+$1,245.8M")).toBeInTheDocument();
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

    await screen.findByText("+$1,245.8M");
    const tableBtn = screen.getByRole("button", { name: "📋 Strike Ladder" });
    fireEvent.click(tableBtn);

    expect(screen.getByText("SPY GEX Strike Exposure Ladder")).toBeInTheDocument();
    expect(screen.getByText("CALL WALL")).toBeInTheDocument();
    expect(screen.getByText("PUT WALL")).toBeInTheDocument();
  });

  it("switches ticker when ticker pill clicked", async () => {
    render(<GexProfileView initialSymbol="SPY" spotPrice={546.50} />);

    await screen.findByText("+$1,245.8M");
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

    await screen.findByText("+$1,245.8M");
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
});

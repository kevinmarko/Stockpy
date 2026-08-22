import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { VpinGauge } from "./VpinGauge";
import { api } from "../../api/client";
import type { VpinMetricsResponse } from "../../api/types";

vi.mock("../../api/client", () => ({
  api: {
    getVpinMetrics: vi.fn(),
  },
}));

const mockLowVpinResponse: VpinMetricsResponse = {
  symbol: "SPY",
  vpin: 0.184,
  regime: "LOW",
  toxicity_percentile: 28,
  bucket_size: 10000,
  num_buckets: 50,
  buckets: Array.from({ length: 50 }, (_, i) => ({
    bucket_index: i + 1,
    buy_volume: 5200,
    sell_volume: 4800,
    total_volume: 10000,
    price_start: 546.0 + i * 0.05,
    price_end: 546.05 + i * 0.05,
    price_change: 0.05,
    imbalance: 400,
    timestamp: new Date().toISOString(),
  })),
  defensive_spread_concession: 0.0,
  warning_message: null,
  as_of: new Date().toISOString(),
};

const mockToxicVpinResponse: VpinMetricsResponse = {
  symbol: "TSLA",
  vpin: 0.428,
  regime: "HIGH_TOXICITY",
  toxicity_percentile: 94,
  bucket_size: 10000,
  num_buckets: 50,
  buckets: Array.from({ length: 50 }, (_, i) => ({
    bucket_index: i + 1,
    buy_volume: 7800,
    sell_volume: 2200,
    total_volume: 10000,
    price_start: 214.0 + i * 0.2,
    price_end: 214.2 + i * 0.2,
    price_change: 0.2,
    imbalance: 5600,
    timestamp: new Date().toISOString(),
  })),
  defensive_spread_concession: 0.08,
  warning_message: "High Microstructure Toxicity (VPIN 42.8% > 35.0%). Institutional informed flow detected.",
  as_of: new Date().toISOString(),
};

const mockUnavailableVpinResponse: VpinMetricsResponse = {
  symbol: "NVDA",
  vpin: null,
  regime: null,
  toxicity_percentile: null,
  bucket_size: 0,
  num_buckets: 20,
  buckets: [],
  defensive_spread_concession: null,
  warning_message:
    "VPIN is unavailable for NVDA -- market data fetch failed for NVDA: MarketDataError: simulated failure. No toxicity measurement is being shown.",
  as_of: new Date().toISOString(),
  data_available: false,
  data_source: null,
  reason: "market data fetch failed for NVDA: MarketDataError: simulated failure",
};

describe("VpinGauge", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getVpinMetrics).mockImplementation(async (sym) => {
      if (sym.toUpperCase() === "TSLA") return mockToxicVpinResponse;
      if (sym.toUpperCase() === "NVDA") return mockUnavailableVpinResponse;
      return mockLowVpinResponse;
    });
  });

  it("renders desk title, Phase 17 badge, and VPIN percentage", async () => {
    render(<VpinGauge initialSymbol="SPY" />);

    expect(
      await screen.findByText(/Options VPIN Toxicity Meter & Microstructure Risk Desk/i)
    ).toBeInTheDocument();
    expect(screen.getByText("Phase 17")).toBeInTheDocument();
    expect(screen.getByText("18.4%")).toBeInTheDocument();
    expect(screen.getByText("LOW")).toBeInTheDocument();
  });

  it("renders toxicity percentile, bucket size, and defensive gate summary", async () => {
    render(<VpinGauge initialSymbol="SPY" />);

    expect(await screen.findByText("28th")).toBeInTheDocument();
    expect(screen.getByText("10,000 sh")).toBeInTheDocument();
    expect(screen.getByText("+0.00 $")).toBeInTheDocument();
  });

  it("displays high toxicity warning banner when VPIN > 35% on TSLA", async () => {
    render(<VpinGauge initialSymbol="TSLA" />);

    expect(
      await screen.findByText(/High Microstructure Toxicity \(VPIN 42\.8% > 35\.0%\)/i)
    ).toBeInTheDocument();
    expect(screen.getByText("42.8%")).toBeInTheDocument();
    expect(screen.getByText("HIGH TOXICITY")).toBeInTheDocument();
    expect(screen.getByText("+0.08 $")).toBeInTheDocument();
  });

  it("switches tickers when ticker pill clicked", async () => {
    const onSelectMock = vi.fn();
    render(<VpinGauge initialSymbol="SPY" onSelectTicker={onSelectMock} />);

    await screen.findByText("18.4%");
    const tslaPill = screen.getByRole("button", { name: "TSLA" });
    fireEvent.click(tslaPill);

    expect(onSelectMock).toHaveBeenCalledWith("TSLA");
    expect(await screen.findByText("42.8%")).toBeInTheDocument();
    expect(screen.getByText("HIGH TOXICITY")).toBeInTheDocument();
  });

  it("renders volume bucket imbalance history and updates on hover", async () => {
    render(<VpinGauge initialSymbol="SPY" />);

    expect(
      await screen.findByText(/Volume Bucket Imbalance History \(N=50\)/i)
    ).toBeInTheDocument();

    // Default hover prompt is visible
    expect(
      screen.getByText(/Hover over any volume bucket bar to inspect detailed price progression/i)
    ).toBeInTheDocument();
  });

  it("renders an honest unavailable state instead of a fabricated reading when data_available is false", async () => {
    render(<VpinGauge initialSymbol="NVDA" />);

    expect(
      await screen.findByText(/VPIN is unavailable for NVDA/i)
    ).toBeInTheDocument();
    // Never falls back to a plausible-looking fake percentage, regime label, or gate value --
    // the toxicity %ile, VPIN readout, and defensive gate all render the same "no data" glyph.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText("UNAVAILABLE")).toBeInTheDocument();
    expect(
      screen.getByText(/No real market data available for NVDA/i)
    ).toBeInTheDocument();
    expect(screen.queryByText("LOW")).not.toBeInTheDocument();
    expect(screen.queryByText("MODERATE")).not.toBeInTheDocument();
    expect(screen.queryByText("HIGH TOXICITY")).not.toBeInTheDocument();
  });

  it("calls onClose when close button is clicked", async () => {
    const handleClose = vi.fn();
    render(<VpinGauge initialSymbol="SPY" onClose={handleClose} />);

    const closeBtn = await screen.findByRole("button", { name: /✕ Close/i });
    fireEvent.click(closeBtn);

    expect(handleClose).toHaveBeenCalled();
  });
});

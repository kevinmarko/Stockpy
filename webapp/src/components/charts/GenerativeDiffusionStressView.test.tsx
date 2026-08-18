import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { GenerativeDiffusionStressView } from "./GenerativeDiffusionStressView";
import { api } from "../../api/client";

vi.mock("../../api/client", () => ({
  api: {
    runDiffusionStressTest: vi.fn(),
  },
}));

describe("GenerativeDiffusionStressView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders form, runs default simulation, and shows all VaR/CVaR risk cards and SVG paths", async () => {
    (api.runDiffusionStressTest as any).mockResolvedValue({
      symbol: "AAPL",
      regime: "vol_shock",
      guidance_scale: 2.0,
      paths: [
        [150, 148, 145, 140],
        [150, 152, 155, 160],
        [150, 149, 147, 142],
        [150, 140, 135, 125],
      ],
      VaR_95: 12.5,
      CVaR_95: 16.8,
      VaR_99: 18.2,
      CVaR_99: 22.4,
      trained_windows: 145,
    });

    render(<GenerativeDiffusionStressView symbol="AAPL" spotPrice={150} />);

    expect(screen.getByText("Generative Diffusion Stress Test: AAPL")).toBeInTheDocument();
    expect(screen.getByText("HIGH VOL")).toBeInTheDocument();
    expect(screen.getByText("Vol Shock (VIX > 40)")).toBeInTheDocument();

    const runBtn = screen.getByText("Run Stress Test");
    fireEvent.click(runBtn);

    expect(screen.getByText("Running...")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Value at Risk (95%)")).toBeInTheDocument();
    });

    // Verify all 4 Risk Metric cards
    expect(screen.getByText("$12.50")).toBeInTheDocument();
    expect(screen.getByText("$16.80")).toBeInTheDocument();
    expect(screen.getByText("$18.20")).toBeInTheDocument();
    expect(screen.getByText("$22.40")).toBeInTheDocument();

    // Verify trained windows badge
    expect(screen.getByText("145 Windows Fitted")).toBeInTheDocument();

    // Verify SVG simulation chart rendered
    expect(screen.getByLabelText("Simulation Paths Chart")).toBeInTheDocument();
    expect(screen.getByText("Guided SDE Simulation Cloud (4 Paths)")).toBeInTheDocument();

    // Verify API called with default parameters
    expect(api.runDiffusionStressTest).toHaveBeenCalledWith({
      symbol: "AAPL",
      spot_price: 150,
      volatility: 0.2,
      drift: 0,
      num_paths: 1000,
      horizon: 30,
      regime: "vol_shock",
      guidance_scale: 2.0,
    });
  });

  it("handles regime selection and guidance scale adjustment", async () => {
    (api.runDiffusionStressTest as any).mockResolvedValue({
      symbol: "TSLA",
      regime: "credit_freeze",
      guidance_scale: 3.5,
      paths: [
        [200, 190, 180, 170],
        [200, 195, 185, 175],
      ],
      VaR_95: 25.0,
      CVaR_95: 32.0,
      VaR_99: 35.0,
      CVaR_99: 40.0,
      trained_windows: 180,
    });

    render(<GenerativeDiffusionStressView symbol="TSLA" spotPrice={200} />);

    // Select "Credit Freeze (High OAS)" regime
    const creditBtn = screen.getByText("Credit Freeze (High OAS)");
    fireEvent.click(creditBtn);

    // Adjust Guidance Scale slider
    const guidanceSlider = screen.getByLabelText("Classifier-Free Guidance Scale");
    fireEvent.change(guidanceSlider, { target: { value: "3.5" } });

    // Run test
    const runBtn = screen.getByText("Run Stress Test");
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(screen.getByText("$25.00")).toBeInTheDocument();
    });

    expect(api.runDiffusionStressTest).toHaveBeenCalledWith({
      symbol: "TSLA",
      spot_price: 200,
      volatility: 0.2,
      drift: 0,
      num_paths: 1000,
      horizon: 30,
      regime: "credit_freeze",
      guidance_scale: 3.5,
    });
  });

  it("handles stagflation and liquidity squeeze regime clicks", async () => {
    (api.runDiffusionStressTest as any).mockResolvedValue({
      symbol: "SPY",
      regime: "liquidity_squeeze",
      guidance_scale: 4.0,
      paths: [[500, 480, 470, 450]],
      VaR_95: 30.0,
      CVaR_95: 45.0,
      VaR_99: 50.0,
      CVaR_99: 60.0,
    });

    render(<GenerativeDiffusionStressView symbol="SPY" spotPrice={500} />);

    const liqBtn = screen.getByText("Liquidity Squeeze");
    fireEvent.click(liqBtn);

    const runBtn = screen.getByText("Run Stress Test");
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(api.runDiffusionStressTest).toHaveBeenCalledWith(
        expect.objectContaining({
          regime: "liquidity_squeeze",
        })
      );
    });
  });

  it("renders error message cleanly on API rejection", async () => {
    (api.runDiffusionStressTest as any).mockRejectedValue(new Error("Insufficient historical data for symbol"));

    render(<GenerativeDiffusionStressView symbol="UNKNOWN" spotPrice={50} />);

    const runBtn = screen.getByText("Run Stress Test");
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(screen.getByText("Insufficient historical data for symbol")).toBeInTheDocument();
    });
  });
});

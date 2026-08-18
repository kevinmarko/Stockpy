import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { HrpPortfolioOptimizerView } from "./HrpPortfolioOptimizerView";
import { api } from "../../api/client";
import { HrpCvarOptimizeResponse } from "../../api/types";

vi.mock("../../api/client", () => ({
  api: {
    optimizeHrpCvar: vi.fn(),
  },
}));

const mockHrpResponse: HrpCvarOptimizeResponse = {
  allocations: [
    { symbol: "AAPL", weight: 0.28 },
    { symbol: "MSFT", weight: 0.24 },
    { symbol: "NVDA", weight: 0.18 },
    { symbol: "JPM", weight: 0.16 },
    { symbol: "V", weight: 0.14 },
  ],
  dendrogram: {
    name: "Root Cluster",
    distance: 0.85,
    children: [
      { name: "Cluster 1", distance: 0.45, children: [{ name: "AAPL", distance: 0 }, { name: "MSFT", distance: 0 }] },
      { name: "NVDA", distance: 0 },
    ],
  },
  expected_return: 0.148,
  cvar_95: 0.0435,
  sharpe_ratio: 1.62,
  turnover: 0.085,
  portfolio_beta: 1.08,
  sector_exposures: {
    Tech: 0.70,
    Financials: 0.30,
  },
  diversification_ratio: 1.45,
  as_of: "2026-08-17T12:00:00Z",
};

describe("HrpPortfolioOptimizerView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders optimizer controls, header, and triggers initial optimization", async () => {
    vi.mocked(api.optimizeHrpCvar).mockResolvedValueOnce(mockHrpResponse);

    render(<HrpPortfolioOptimizerView symbols={["AAPL", "MSFT", "NVDA", "JPM", "V"]} />);

    expect(
      screen.getByText("Turnover-Regularized HRP-CVaR Optimizer")
    ).toBeInTheDocument();
    expect(screen.getByText("Phase 35 Engine")).toBeInTheDocument();
    expect(screen.getByTestId("run-optimize-btn")).toBeInTheDocument();

    // Verify initial controls are rendered
    expect(screen.getByLabelText("Turnover Penalty Slider")).toBeInTheDocument();
    expect(screen.getByLabelText("Max Asset Weight Slider")).toBeInTheDocument();
    expect(screen.getByLabelText("Target Beta Minimum")).toBeInTheDocument();
    expect(screen.getByLabelText("Target Beta Maximum")).toBeInTheDocument();

    await waitFor(() => {
      expect(api.optimizeHrpCvar).toHaveBeenCalledTimes(1);
    });
  });

  it("renders KPI metrics properly after optimization", async () => {
    vi.mocked(api.optimizeHrpCvar).mockResolvedValueOnce(mockHrpResponse);

    render(<HrpPortfolioOptimizerView symbols={["AAPL", "MSFT", "NVDA", "JPM", "V"]} />);

    await waitFor(() => {
      expect(screen.getByTestId("hrp-kpis")).toBeInTheDocument();
    });

    // Turnover 8.5%
    expect(screen.getByTestId("kpi-turnover")).toHaveTextContent("8.5%");
    // CVaR 4.35%
    expect(screen.getByTestId("kpi-cvar")).toHaveTextContent("4.35%");
    // Beta 1.08
    expect(screen.getByTestId("kpi-beta")).toHaveTextContent("1.08");
    // Diversification ratio 1.45x
    expect(screen.getByTestId("kpi-div-ratio")).toHaveTextContent("1.45x");

    // Sector Exposures & Dendrogram
    expect(screen.getByText("Sector Exposures vs Configured Caps")).toBeInTheDocument();
    expect(screen.getByText("Root Cluster")).toBeInTheDocument();
  });

  it("handles turnover slider and sector cap changes and triggers re-optimization", async () => {
    vi.mocked(api.optimizeHrpCvar).mockResolvedValue(mockHrpResponse);

    render(<HrpPortfolioOptimizerView symbols={["AAPL", "MSFT", "NVDA", "JPM", "V"]} />);

    await waitFor(() => {
      expect(api.optimizeHrpCvar).toHaveBeenCalledTimes(1);
    });

    const turnoverSlider = screen.getByLabelText("Turnover Penalty Slider");
    fireEvent.change(turnoverSlider, { target: { value: "0.25" } });
    expect(screen.getByTestId("turnover-lambda-val")).toHaveTextContent("0.25");

    const maxWeightSlider = screen.getByLabelText("Max Asset Weight Slider");
    fireEvent.change(maxWeightSlider, { target: { value: "0.45" } });
    expect(screen.getByTestId("max-weight-val")).toHaveTextContent("45%");

    const techCapInput = screen.getByLabelText("Tech Sector Cap");
    fireEvent.change(techCapInput, { target: { value: "0.40" } });

    // Click Run Optimization
    const runBtn = screen.getByTestId("run-optimize-btn");
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(api.optimizeHrpCvar).toHaveBeenCalledTimes(2);
      expect(api.optimizeHrpCvar).toHaveBeenLastCalledWith(
        expect.objectContaining({
          lambda_turnover: 0.25,
          sector_caps: expect.objectContaining({ Tech: 0.4 }),
        })
      );
    });
  });

  it("handles optimization error gracefully", async () => {
    vi.mocked(api.optimizeHrpCvar).mockRejectedValueOnce(
      new Error("Overlapping data insufficient for covariance matrix")
    );

    render(<HrpPortfolioOptimizerView symbols={["AAPL", "MSFT"]} />);

    await waitFor(() => {
      expect(
        screen.getByText(/Overlapping data insufficient for covariance matrix/)
      ).toBeInTheDocument();
    });
  });
});

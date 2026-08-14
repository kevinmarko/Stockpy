import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { ScenarioHeatmap } from "./ScenarioHeatmap";
import { api } from "../../api/client";
import type { ScenarioMatrixResponse } from "../../api/types";

vi.mock("../../api/client", () => ({
  api: {
    getScenarioMatrix: vi.fn(),
  },
}));

const mockScenarioData: ScenarioMatrixResponse = {
  spot_shifts: [-0.10, -0.05, 0, 0.05, 0.10],
  iv_shifts: [-0.20, 0, 0.20],
  time_slices: [0, 7, 14, 21],
  current_portfolio_value: 100000,
  matrix: [
    {
      spot_shift_pct: 0,
      iv_shift_pct: 0,
      days_forward: 0,
      spot_price: 500,
      portfolio_value: 100000,
      pnl_dollar: 0,
      pnl_pct: 0,
      net_delta: 50,
      net_gamma: 0.015,
      net_theta: 20,
      net_vega: 10,
    },
    {
      spot_shift_pct: 0.05,
      iv_shift_pct: 0,
      days_forward: 0,
      spot_price: 525,
      portfolio_value: 101250,
      pnl_dollar: 1250,
      pnl_pct: 0.0125,
      net_delta: 55,
      net_gamma: 0.014,
      net_theta: 19,
      net_vega: 9.8,
    },
    {
      spot_shift_pct: -0.05,
      iv_shift_pct: 0,
      days_forward: 0,
      spot_price: 475,
      portfolio_value: 98750,
      pnl_dollar: -1250,
      pnl_pct: -0.0125,
      net_delta: 45,
      net_gamma: 0.016,
      net_theta: 21,
      net_vega: 10.2,
    },
  ],
  historical_scenarios: [
    {
      id: "lehman-2008",
      name: "Lehman Crash",
      description: "-15% Spot, +50% IV",
      spot_shift_pct: -0.15,
      iv_shift_pct: 0.50,
      projected_pnl_dollar: -3500,
      projected_pnl_pct: -0.035,
    },
  ],
};

describe("ScenarioHeatmap", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getScenarioMatrix).mockResolvedValue(mockScenarioData);
  });

  it("renders scenario matrix title and controls", async () => {
    render(<ScenarioHeatmap initialData={mockScenarioData} />);

    expect(screen.getByText(/Multi-Dimensional Scenario Matrix/i)).toBeInTheDocument();
    expect(screen.getByText("Today (T+0)")).toBeInTheDocument();
    expect(screen.getByText("+7 Days (T+7)")).toBeInTheDocument();
    expect(screen.getByText("P&L ($)")).toBeInTheDocument();
    expect(screen.getByText("Delta (Δ)")).toBeInTheDocument();
  });

  it("allows switching metric view to Delta and Theta", async () => {
    render(<ScenarioHeatmap initialData={mockScenarioData} />);

    const deltaBtn = screen.getByText("Delta (Δ)");
    fireEvent.click(deltaBtn);

    expect(screen.getByText("+50.0Δ")).toBeInTheDocument();

    const thetaBtn = screen.getByText("Theta (Θ)");
    fireEvent.click(thetaBtn);

    expect(screen.getByText("+$20.0Θ")).toBeInTheDocument();
  });

  it("allows selecting historical stress scenario", async () => {
    render(<ScenarioHeatmap initialData={mockScenarioData} />);

    expect(screen.getByText("Lehman Crash")).toBeInTheDocument();
    expect(screen.getByText("-$3,500")).toBeInTheDocument();

    const lehmanCard = screen.getByText("Lehman Crash");
    fireEvent.click(lehmanCard);

    expect(screen.getByText(/Impact: -3.50% Equity Shock/i)).toBeInTheDocument();
  });

  it("triggers refresh callback", async () => {
    const handleRefresh = vi.fn();
    render(<ScenarioHeatmap initialData={mockScenarioData} onRefresh={handleRefresh} />);

    const refreshBtn = await screen.findByRole("button", { name: /Refresh/i });
    await waitFor(() => {
      expect(refreshBtn).not.toBeDisabled();
    });
    fireEvent.click(refreshBtn);

    await waitFor(() => {
      expect(handleRefresh).toHaveBeenCalled();
      expect(api.getScenarioMatrix).toHaveBeenCalled();
    });
  });
});

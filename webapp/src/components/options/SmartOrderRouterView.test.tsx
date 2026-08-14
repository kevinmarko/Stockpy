import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { SmartOrderRouterView } from "./SmartOrderRouterView";
import { api } from "../../api/client";
import type {
  SorAnalysisResponse,
  LeggingSimulationResponse,
} from "../../api/types";

vi.mock("../../api/client", () => ({
  api: {
    analyzeOptionsRouting: vi.fn(),
    simulateOptionsLegging: vi.fn(),
  },
}));

const mockSorResponse: SorAnalysisResponse = {
  symbol: "SPY",
  recommended_route: "LEG_PASSIVE_FIRST",
  cob_net_price: 1.30,
  cob_natural_price: 1.45,
  synthetic_net_price: 1.18,
  expected_savings: 27.0,
  hung_leg_probability: 0.031,
  adverse_selection_cost: 1.49,
  latency_ms: 250,
  legs_breakdown: [
    {
      strike: 540,
      option_type: "PUT",
      action: "SELL",
      bid: 3.10,
      ask: 3.25,
      mid: 3.175,
      fill_priority: 1,
      fill_style: "PASSIVE",
    },
    {
      strike: 535,
      option_type: "PUT",
      action: "BUY",
      bid: 1.80,
      ask: 1.95,
      mid: 1.875,
      fill_priority: 2,
      fill_style: "ACTIVE",
    },
  ],
  rationale: "Synthetic legging captures $27.00 edge with low hung leg hazard (3.1% @ 250ms).",
  as_of: new Date().toISOString(),
};

const mockSimResponse: LeggingSimulationResponse = {
  symbol: "SPY",
  num_simulations: 1000,
  latency_seconds: 0.25,
  hung_leg_rate: 0.032,
  expected_edge_dollars: 23.18,
  edge_std_dollars: 14.20,
  worst_case_loss_dollars: -58.00,
  p95_adverse_selection: -26.50,
  pnl_distribution: [
    { bin_edge: -40, count: 18, probability: 0.018 },
    { bin_edge: -20, count: 54, probability: 0.054 },
    { bin_edge: 0, count: 120, probability: 0.120 },
    { bin_edge: 20, count: 245, probability: 0.245 },
    { bin_edge: 40, count: 80, probability: 0.080 },
  ],
  latency_curve: [
    { latency_ms: 50, hung_leg_rate: 0.017, expected_edge: 28.08 },
    { latency_ms: 250, hung_leg_rate: 0.028, expected_edge: 26.40 },
    { latency_ms: 500, hung_leg_rate: 0.041, expected_edge: 24.30 },
    { latency_ms: 1000, hung_leg_rate: 0.067, expected_edge: 20.10 },
  ],
  as_of: new Date().toISOString(),
};

describe("SmartOrderRouterView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.analyzeOptionsRouting).mockResolvedValue(mockSorResponse);
    vi.mocked(api.simulateOptionsLegging).mockResolvedValue(mockSimResponse);
  });

  it("renders SOR desk title, Phase 18 badge, and execution prices", async () => {
    render(<SmartOrderRouterView initialSymbol="SPY" spotPrice={546.50} />);

    expect(
      await screen.findByText(/Multi-Leg Smart Order Router \(SOR\) & Legging Desk/i)
    ).toBeInTheDocument();
    expect(screen.getByText("Phase 18")).toBeInTheDocument();
    expect(screen.getByText("$1.30")).toBeInTheDocument(); // COB net
    expect(screen.getByText("$1.45")).toBeInTheDocument(); // COB natural
    expect(screen.getByText("$1.18")).toBeInTheDocument(); // Synthetic net
    expect(screen.getByText("+$27.00 Edge")).toBeInTheDocument();
  });

  it("renders leg fill priorities and fill styles in sequence table", async () => {
    render(<SmartOrderRouterView initialSymbol="SPY" spotPrice={546.50} />);

    expect(await screen.findByText("540 PUT")).toBeInTheDocument();
    expect(screen.getByText("535 PUT")).toBeInTheDocument();
    expect(screen.getByText("Priority #1")).toBeInTheDocument();
    expect(screen.getByText("Priority #2")).toBeInTheDocument();
    expect(screen.getByText("PASSIVE")).toBeInTheDocument();
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
  });

  it("renders hung leg hazard and Monte Carlo KPIs", async () => {
    render(<SmartOrderRouterView initialSymbol="SPY" spotPrice={546.50} />);

    expect(await screen.findByText("3.2%")).toBeInTheDocument(); // Hung leg rate
    expect(screen.getByText("+$23.18")).toBeInTheDocument(); // Expected net edge
    expect(screen.getByText("$-26.50")).toBeInTheDocument(); // P95 adverse selection
  });

  it("switches strategy preset when preset button clicked", async () => {
    render(<SmartOrderRouterView initialSymbol="SPY" spotPrice={546.50} />);

    await screen.findByText("$1.30");
    const condorBtn = screen.getByRole("button", { name: "Iron Condor" });
    fireEvent.click(condorBtn);

    await waitFor(() => {
      expect(api.analyzeOptionsRouting).toHaveBeenCalled();
    });
  });

  it("executes 1-click routing and triggers onRouteOrder callback", async () => {
    const handleRouteMock = vi.fn();
    render(
      <SmartOrderRouterView
        initialSymbol="SPY"
        spotPrice={546.50}
        onRouteOrder={handleRouteMock}
      />
    );

    const routeBtn = await screen.findByRole("button", {
      name: /🚀 Route & Execute via LEG PASSIVE FIRST/i,
    });
    fireEvent.click(routeBtn);

    expect(handleRouteMock).toHaveBeenCalledWith(
      "LEG_PASSIVE_FIRST",
      expect.objectContaining({
        recommended_route: "LEG_PASSIVE_FIRST",
        expected_savings: 27.0,
      })
    );

    expect(
      await screen.findByText(/Order routed via Synthetic Legging \(Passive First\)/i)
    ).toBeInTheDocument();
  });

  it("calls onClose when close button clicked", async () => {
    const handleClose = vi.fn();
    render(<SmartOrderRouterView onClose={handleClose} />);

    const closeBtn = await screen.findByText("✕ Close");
    fireEvent.click(closeBtn);

    expect(handleClose).toHaveBeenCalled();
  });
});

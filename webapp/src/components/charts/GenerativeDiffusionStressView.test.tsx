import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { GenerativeDiffusionStressView } from "./GenerativeDiffusionStressView";
import { api } from "../../api/client";

vi.mock("../../api/client", () => ({
  api: {
    runDiffusionStressTest: vi.fn(),
  },
}));

describe("GenerativeDiffusionStressView", () => {
  it("renders form, runs simulation, and shows results", async () => {
    (api.runDiffusionStressTest as any).mockResolvedValue({
      symbol: "AAPL",
      horizon_days: 30,
      paths_simulated: 1000,
      cvar_95: 0.18,
      var_95: 0.12,
      expected_shortfall: 0.15,
      max_drawdown_distribution: [0.1, 0.2, 0.3],
      terminal_price_distribution: [80, 100, 120],
      crash_probabilities: { "-10%": 0.1 },
      sample_paths: [],
    });

    render(<GenerativeDiffusionStressView symbol="AAPL" />);

    expect(screen.getByText("🌪️ Generative Diffusion Stress Test: AAPL")).toBeInTheDocument();
    
    const runBtn = screen.getByText("Run Stress Test");
    fireEvent.click(runBtn);

    expect(screen.getByText("Running...")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Value at Risk (95%)")).toBeInTheDocument();
    });

    expect(screen.getByText("12.00%")).toBeInTheDocument();
    expect(screen.getByText("-10% drop")).toBeInTheDocument();
    expect(api.runDiffusionStressTest).toHaveBeenCalledWith({
      symbol: "AAPL",
      drift: 0,
      volatility: 0.2,
      jump_intensity: 0.01,
      jump_mean: -0.05,
      jump_std: 0.1,
      paths: 1000,
      horizon_days: 30,
    });
  });
});

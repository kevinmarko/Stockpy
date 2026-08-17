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
      paths: [
        [100, 98, 95, 90],
        [100, 102, 105, 110],
        [100, 99, 97, 92],
      ],
      VaR_95: 8.5,
      CVaR_95: 10.2,
    });

    render(<GenerativeDiffusionStressView symbol="AAPL" spotPrice={100} />);

    expect(screen.getByText("🌪️ Generative Diffusion Stress Test: AAPL")).toBeInTheDocument();

    const runBtn = screen.getByText("Run Stress Test");
    fireEvent.click(runBtn);

    expect(screen.getByText("Running...")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Value at Risk (95%)")).toBeInTheDocument();
    });

    expect(screen.getByText("$8.50")).toBeInTheDocument();
    expect(screen.getByText("$10.20")).toBeInTheDocument();
    expect(api.runDiffusionStressTest).toHaveBeenCalledWith({
      symbol: "AAPL",
      spot_price: 100,
      volatility: 0.2,
      drift: 0,
      num_paths: 1000,
      horizon: 30,
    });
  });
});

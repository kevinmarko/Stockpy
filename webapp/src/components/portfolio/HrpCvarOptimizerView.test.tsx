import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { HrpCvarOptimizerView } from "./HrpCvarOptimizerView";
import { api } from "../../api/client";

vi.mock("../../api/client", () => ({
  api: {
    optimizeHrpCvar: vi.fn(),
  },
}));

describe("HrpCvarOptimizerView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders correctly and calls api on button click", async () => {
    vi.mocked(api.optimizeHrpCvar).mockResolvedValueOnce({
      allocations: [
        { symbol: "AAPL", weight: 0.6 },
        { symbol: "MSFT", weight: 0.4 },
      ],
      dendrogram: {
        name: "root",
        distance: 1.0,
        children: [
          { name: "AAPL", distance: 0 },
          { name: "MSFT", distance: 0 },
        ],
      },
      expected_return: 0.15,
      cvar_95: 0.08,
      sharpe_ratio: 1.5,
    });

    render(<HrpCvarOptimizerView symbols={["AAPL", "MSFT"]} />);
    
    expect(screen.getByText("HRP CVaR Optimizer")).toBeInTheDocument();
    
    const btn = screen.getByRole("button", { name: "Run Optimization" });
    fireEvent.click(btn);
    
    expect(btn).toHaveTextContent("Optimizing...");
    
    await waitFor(() => {
      expect(screen.getByText("Asset Allocations")).toBeInTheDocument();
    });
    
    expect(screen.getByText("Expected Return: 15.00%")).toBeInTheDocument();
    expect(screen.getByText("CVaR (95%): 8.00%")).toBeInTheDocument();
    expect(screen.getByText("Sharpe Ratio: 1.50")).toBeInTheDocument();
    expect(screen.getByText("Dendrogram Clustering")).toBeInTheDocument();
    expect(screen.getByText("root")).toBeInTheDocument();
  });

  it("handles empty symbols", () => {
    render(<HrpCvarOptimizerView symbols={[]} />);
    const btn = screen.getByRole("button", { name: "Run Optimization" });
    expect(btn).toBeDisabled();
  });

  it("handles api error", async () => {
    vi.mocked(api.optimizeHrpCvar).mockRejectedValueOnce(new Error("Optimization failed"));
    render(<HrpCvarOptimizerView symbols={["AAPL"]} />);
    
    const btn = screen.getByRole("button", { name: "Run Optimization" });
    fireEvent.click(btn);
    
    await waitFor(() => {
      expect(screen.getByText("Optimization failed")).toBeInTheDocument();
    });
  });
});

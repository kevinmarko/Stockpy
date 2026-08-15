import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { AlmgrenChrissRouterView } from "./AlmgrenChrissRouterView";
import { api } from "../../api/client";

vi.mock("../../api/client", () => ({
  api: {
    optimizeAlmgrenChriss: vi.fn(),
  },
}));

describe("AlmgrenChrissRouterView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders correctly and calls api on button click", async () => {
    vi.mocked(api.optimizeAlmgrenChriss).mockResolvedValueOnce({
      symbol: "AAPL",
      expected_shortfall: 1.25,
      variance: 0.8,
      half_life: 5.0,
      trajectory: [
        { step: 1, shares_remaining: 100, trade_size: 10, expected_price: 150 },
        { step: 2, shares_remaining: 90, trade_size: 10, expected_price: 150 },
      ],
      expected_trajectory: [
        { step: 1, shares_remaining: 100, trade_size: 10, expected_price: 150 },
        { step: 2, shares_remaining: 90, trade_size: 10, expected_price: 150 },
      ],
    });

    render(<AlmgrenChrissRouterView symbol="AAPL" quantity={100} />);
    
    expect(screen.getByText("Almgren-Chriss Execution Router")).toBeInTheDocument();
    
    const btn = screen.getByRole("button", { name: "Calculate Execution Trajectory" });
    fireEvent.click(btn);
    
    expect(btn).toHaveTextContent("Calculating...");
    
    await waitFor(() => {
      expect(screen.getByText("Expected Shortfall")).toBeInTheDocument();
    });
    
    expect(screen.getByText("$1.25")).toBeInTheDocument();
    expect(screen.getByText("0.8000")).toBeInTheDocument();
    expect(screen.getByText("5.0")).toBeInTheDocument();
  });

  it("handles empty symbol, zero, or negative quantity", () => {
    const { rerender } = render(<AlmgrenChrissRouterView symbol="" quantity={100} />);
    expect(screen.getByRole("button", { name: "Calculate Execution Trajectory" })).toBeDisabled();

    rerender(<AlmgrenChrissRouterView symbol="AAPL" quantity={0} />);
    expect(screen.getByRole("button", { name: "Calculate Execution Trajectory" })).toBeDisabled();

    rerender(<AlmgrenChrissRouterView symbol="AAPL" quantity={-50} />);
    expect(screen.getByRole("button", { name: "Calculate Execution Trajectory" })).toBeDisabled();
  });

  it("handles api error", async () => {
    vi.mocked(api.optimizeAlmgrenChriss).mockRejectedValueOnce(new Error("Calculation failed"));
    render(<AlmgrenChrissRouterView symbol="AAPL" quantity={100} />);
    
    const btn = screen.getByRole("button", { name: "Calculate Execution Trajectory" });
    fireEvent.click(btn);
    
    await waitFor(() => {
      expect(screen.getByText("Calculation failed")).toBeInTheDocument();
    });
  });

  it("handles empty trajectory array in response", async () => {
    vi.mocked(api.optimizeAlmgrenChriss).mockResolvedValueOnce({
      symbol: "AAPL",
      expected_shortfall: 0,
      variance: 0,
      half_life: 0,
      trajectory: [],
      expected_trajectory: [],
    });
    render(<AlmgrenChrissRouterView symbol="AAPL" quantity={100} />);
    
    const btn = screen.getByRole("button", { name: "Calculate Execution Trajectory" });
    fireEvent.click(btn);
    
    await waitFor(() => {
      expect(screen.getByText("Expected Shortfall")).toBeInTheDocument();
    });
  });
});

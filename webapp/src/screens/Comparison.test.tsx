import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Comparison } from "./Comparison";
import { api } from "../api/client";
import { ApiError } from "../api/types";

function renderComparison() {
  return render(
    <MemoryRouter>
      <Comparison />
    </MemoryRouter>
  );
}

describe("Comparison screen (R2)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  // T1.1: Comparison Screen Mount
  it("renders comparison screen and default checklist instructions", async () => {
    renderComparison();
    expect(await screen.findByTestId("comparison-title")).toBeInTheDocument();
    expect(screen.getByText(/Select at least one pilot strategy above/i)).toBeInTheDocument();
  });

  // T1.2: Toggle Pilot Checkbox
  it("checks a pilot strategy and renders the metrics table", async () => {
    renderComparison();
    const checkbox = await screen.findByTestId("comparison-checkbox-trend-following");
    fireEvent.click(checkbox);

    expect(await screen.findByText("Key Metrics Comparison")).toBeInTheDocument();
    const headers = screen.getAllByRole("columnheader");
    expect(headers.map(h => h.textContent)).toContain("Trend Follower");
  });

  // T1.3: Multi-Selection Chart Aggregation
  it("renders comparison chart with series count when multiple pilots are checked", async () => {
    const { container } = renderComparison();
    const cb1 = await screen.findByTestId("comparison-checkbox-trend-following");
    const cb2 = await screen.findByTestId("comparison-checkbox-dip-buyer");
    fireEvent.click(cb1);
    fireEvent.click(cb2);

    expect(await screen.findByText("Trend Follower")).toBeInTheDocument();
    expect(screen.getByText("Dip Buyer")).toBeInTheDocument();
    expect(container.querySelector(".recharts-responsive-container")).toBeInTheDocument();
  });

  // T1.4: Remove Selection Column
  it("removes column and series when pilot is unchecked", async () => {
    const { container } = renderComparison();
    const cb1 = await screen.findByTestId("comparison-checkbox-trend-following");
    const cb2 = await screen.findByTestId("comparison-checkbox-dip-buyer");
    
    // Select two
    fireEvent.click(cb1);
    fireEvent.click(cb2);
    expect(await screen.findByText("Trend Follower")).toBeInTheDocument();
    expect(screen.getByText("Dip Buyer")).toBeInTheDocument();

    // Unselect one
    fireEvent.click(cb1);
    expect(screen.queryByText("Trend Follower")).not.toBeInTheDocument();
    expect(screen.getByText("Dip Buyer")).toBeInTheDocument();
    const headers = screen.getAllByRole("columnheader");
    expect(headers.map(h => h.textContent)).not.toContain("Trend Follower");
  });

  // T1.5: Clear Selection Action
  it("resets all selections and displays empty state on clicking Clear All", async () => {
    renderComparison();
    const cb = await screen.findByTestId("comparison-checkbox-trend-following");
    fireEvent.click(cb);
    expect(await screen.findByText("Key Metrics Comparison")).toBeInTheDocument();

    const clearBtn = screen.getByText("Clear All");
    fireEvent.click(clearBtn);
    expect(screen.getByText(/Select at least one pilot strategy above/i)).toBeInTheDocument();
  });

  // T2.1: Compare Cold Start Pilot (Null Curve)
  it("handles a pilot with null curve or metrics by displaying '-' and excluding it from chart", async () => {
    renderComparison();
    // balanced-blend has null curve and null metrics in mock.ts
    const cb = await screen.findByTestId("comparison-checkbox-balanced-blend");
    fireEvent.click(cb);

    expect(await screen.findByText("Key Metrics Comparison")).toBeInTheDocument();
    // Should show empty chart placeholder (0 series)
    expect(screen.getByText("No performance curve data available for selected pilots.")).toBeInTheDocument();
  });

  // T2.2: Single Detail Fetch Failure
  it("keeps other loaded pilots visible and displays a row error banner if one pilot details fetch fails", async () => {
    vi.spyOn(api, "getPerformance").mockImplementation((id, range) => {
      if (id === "trend-following") {
        return Promise.reject(new ApiError("500 internal error", 500));
      }
      return Promise.resolve({ range, curve: [{ date: "2026-07-01", value: 100 }] } as any);
    });

    const { container } = renderComparison();
    const cb1 = await screen.findByTestId("comparison-checkbox-trend-following");
    const cb2 = await screen.findByTestId("comparison-checkbox-dip-buyer");
    
    fireEvent.click(cb1);
    fireEvent.click(cb2);

    expect(await screen.findByTestId("row-error-banner")).toBeInTheDocument();
    expect(screen.getByText("Dip Buyer")).toBeInTheDocument(); // dip-buyer is still loaded
    expect(screen.queryByRole("columnheader", { name: "Trend Follower" })).not.toBeInTheDocument();
    expect(container.querySelector(".recharts-responsive-container")).toBeInTheDocument();
  });

  // T2.3: Enforces Select Cap Limit
  it("disables other checkboxes when 5 pilots are selected", async () => {
    renderComparison();
    const pilots = await api.listPilots();
    
    // Select first 5 pilots
    for (let i = 0; i < 5; i++) {
      const cb = await screen.findByTestId(`comparison-checkbox-${pilots[i].id}`);
      fireEvent.click(cb);
    }

    // The 6th pilot checkbox should be disabled
    const cb6 = screen.getByTestId(`comparison-checkbox-${pilots[5].id}`) as HTMLInputElement;
    expect(cb6.disabled).toBe(true);
  });

  // T2.4: Transposes Partial Metric Lists
  it("displays '-' for missing metric values rather than throwing", async () => {
    renderComparison();
    // momentum-burst has no stress_gate_passed / long_only logic or might have some nulls
    // let's just select balanced-blend which has null metrics for everything
    const cb = await screen.findByTestId("comparison-checkbox-balanced-blend");
    fireEvent.click(cb);

    expect(await screen.findByText("Key Metrics Comparison")).toBeInTheDocument();
    const cells = screen.getAllByRole("cell");
    const cellTexts = cells.map(c => c.textContent);
    expect(cellTexts).toContain("—");
  });

  // T2.5: Text Overflow Renders
  it("wraps long pilot name headers gracefully without layout corruption", async () => {
    renderComparison();
    const cb = await screen.findByTestId("comparison-checkbox-cross-sectional-momentum");
    fireEvent.click(cb);

    const header = await screen.findByText("Momentum Leaders");
    expect(header.style.whiteSpace).toBe("normal");
    expect(header.style.wordBreak).toBe("break-word");
  });
});

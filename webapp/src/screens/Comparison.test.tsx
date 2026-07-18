import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
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

  it("renders the recommended-stocks list", async () => {
    renderComparison();
    const recs = await screen.findByTestId("recommended-stocks");
    expect(await within(recs).findByTestId("rec-row-NVDA")).toBeInTheDocument();
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

    // Both selected pilots (real curves) become table columns.
    expect(await screen.findByRole("columnheader", { name: "Trend Follower" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Dip Buyer" })).toBeInTheDocument();
    // Chart is shown (not the empty placeholder) once both real curves load.
    await waitFor(() =>
      expect(container.querySelector(".recharts-responsive-container")).toBeInTheDocument()
    );
    // Neither has a null curve, so no honest "no backtest series" note appears.
    expect(screen.queryByTestId("no-series-note")).not.toBeInTheDocument();
  });

  // T1.4: Remove Selection Column
  it("removes column and series when pilot is unchecked", async () => {
    renderComparison();
    const cb1 = await screen.findByTestId("comparison-checkbox-trend-following");
    const cb2 = await screen.findByTestId("comparison-checkbox-dip-buyer");

    // Select two
    fireEvent.click(cb1);
    fireEvent.click(cb2);
    expect(await screen.findByRole("columnheader", { name: "Trend Follower" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Dip Buyer" })).toBeInTheDocument();

    // Unselect one
    fireEvent.click(cb1);
    expect(screen.queryByRole("columnheader", { name: "Trend Follower" })).not.toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Dip Buyer" })).toBeInTheDocument();
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

  // T2.1 (HONESTY): a null-curve pilot stays in the metrics table, renders an
  // honest "no backtest series" note, and NEVER gets a fabricated chart line.
  it("keeps a null-curve pilot in the table, shows an honest note, and draws no line for it", async () => {
    const { container } = renderComparison();
    // value-quality has curve:null (+ reason) in mock.ts
    const cb = await screen.findByTestId("comparison-checkbox-value-quality");
    fireEvent.click(cb);

    // Metrics table still lists it as a column.
    expect(await screen.findByText("Key Metrics Comparison")).toBeInTheDocument();
    const headers = screen.getAllByRole("columnheader");
    expect(headers.map(h => h.textContent)).toContain("Value + Quality");

    // Honest "no backtest series" note names the pilot.
    const note = await screen.findByTestId("no-series-note");
    expect(note).toHaveTextContent(/No backtest series for:/i);
    expect(note).toHaveTextContent("Value + Quality");

    // Empty chart placeholder (0 real curves) and NO fabricated recharts line.
    expect(screen.getByText("No performance curve data available for selected pilots.")).toBeInTheDocument();
    expect(container.querySelector(".recharts-line")).not.toBeInTheDocument();
  });

  // T2.1b: a null-curve pilot selected ALONGSIDE a real-curve pilot keeps the
  // chart (real curve renders), stays in the table, and is named ONLY in the
  // honest note — never drawn as a phantom line.
  it("keeps the chart for the real-curve pilot while naming only the null-curve pilot in the note", async () => {
    const { container } = renderComparison();
    const real = await screen.findByTestId("comparison-checkbox-trend-following");
    const nullCurve = await screen.findByTestId("comparison-checkbox-value-quality");
    fireEvent.click(real);
    fireEvent.click(nullCurve);

    // Both appear as table columns (null-curve pilot keeps its metrics row).
    expect(await screen.findByRole("columnheader", { name: "Trend Follower" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Value + Quality" })).toBeInTheDocument();

    // Real curve renders the chart; the empty placeholder is NOT shown.
    await waitFor(() =>
      expect(container.querySelector(".recharts-responsive-container")).toBeInTheDocument()
    );
    expect(
      screen.queryByText("No performance curve data available for selected pilots.")
    ).not.toBeInTheDocument();

    // The null-curve pilot is named in the honest note; the real-curve one is not.
    const note = await screen.findByTestId("no-series-note");
    expect(note).toHaveTextContent("Value + Quality");
    expect(note).not.toHaveTextContent("Trend Follower");
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
    // dip-buyer is still loaded as a column; the errored pilot is excluded.
    expect(screen.getByRole("columnheader", { name: "Dip Buyer" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Trend Follower" })).not.toBeInTheDocument();
    await waitFor(() =>
      expect(container.querySelector(".recharts-responsive-container")).toBeInTheDocument()
    );
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
    // balanced-blend has null metrics for everything
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

    const header = await screen.findByRole("columnheader", { name: "Momentum Leaders" });
    expect(header.style.whiteSpace).toBe("normal");
    expect(header.style.wordBreak).toBe("break-word");
  });
});

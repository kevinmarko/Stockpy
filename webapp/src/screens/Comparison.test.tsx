import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
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
    expect(screen.getByText(/Select at least one pilot strategy in the left pane/i)).toBeInTheDocument();
  });

  it("renders the recommended-stocks list", async () => {
    renderComparison();
    const recs = await screen.findByTestId("recommended-stocks");
    expect(await within(recs).findByTestId("rec-row-NVDA")).toBeInTheDocument();
  });

  // Symbol-vs-symbol comparison — a separate card from Pilot-vs-Pilot above,
  // independent of any pilot being selected. Full behavior coverage lives in
  // SymbolComparison.test.tsx; this is just the mount/wiring smoke test.
  it("renders the symbol comparison card independent of pilot selection", async () => {
    renderComparison();
    expect(await screen.findByTestId("symbol-comparison")).toBeInTheDocument();
    expect(screen.getByText("Symbol Comparison")).toBeInTheDocument();
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
    expect(screen.getByText(/Select at least one pilot strategy in the left pane/i)).toBeInTheDocument();
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
    // findByText (not getByText): the placeholder and the note above don't
    // necessarily commit in the same render pass, so a synchronous query here
    // was a race that CI's timing tripped more reliably than local runs.
    expect(await screen.findByText("No performance curve data available for selected pilots.")).toBeInTheDocument();
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

  // T3.1: Category accordions group pilots and show a live, per-category
  // selected count that updates as checkboxes inside that category toggle.
  it("groups pilots into category accordions with a live per-category selected count", async () => {
    const { container } = renderComparison();
    await screen.findByTestId("comparison-checkbox-trend-following");

    const momentumSummary = Array.from(container.querySelectorAll("summary")).find((el) =>
      el.textContent?.startsWith("Momentum ")
    );
    expect(momentumSummary).toBeDefined();
    expect(momentumSummary?.textContent).toMatch(/\(0\/5\)/);

    fireEvent.click(screen.getByTestId("comparison-checkbox-trend-following"));
    expect(momentumSummary?.textContent).toMatch(/\(1\/5\)/);

    // A pilot in a different category (Mean Reversion) never bleeds into
    // Momentum's count.
    fireEvent.click(await screen.findByTestId("comparison-checkbox-dip-buyer"));
    expect(momentumSummary?.textContent).toMatch(/\(1\/5\)/);
  });

  // T3.2: Quick-add dropdown is a second path to the same selection state as
  // the checkboxes -- it must stay in sync both ways.
  it("selecting a pilot from the quick-add dropdown checks it and removes it from the dropdown's own options", async () => {
    renderComparison();
    // Wait for the pilot list (and thus the dropdown's <option>s) to load --
    // setting .value to an option that doesn't exist yet is a silent no-op.
    await screen.findByTestId("comparison-checkbox-trend-following");
    const dropdown = await screen.findByTestId("comparison-quick-add");
    fireEvent.change(dropdown, { target: { value: "trend-following" } });

    // Toggle renders `<button role="switch" aria-checked>`, not a native
    // checkbox input -- see Toggle.tsx's own doc comment.
    const toggle = await screen.findByTestId("comparison-checkbox-trend-following");
    expect(toggle).toHaveAttribute("aria-checked", "true");

    // The now-selected pilot's own <option> is disabled so it can't be
    // "added" a second time from the dropdown.
    const option = within(dropdown as HTMLSelectElement).getByText("Trend Follower") as HTMLOptionElement;
    expect(option.disabled).toBe(true);

    // The dropdown itself resets to its placeholder rather than sticking on
    // the just-picked pilot (it's controlled with value="").
    expect((dropdown as HTMLSelectElement).value).toBe("");
  });

  // T3.3: The quick-add dropdown enforces the same 5-pilot cap as the
  // checkboxes, not a separate/forgotten limit.
  it("disables the quick-add dropdown once 5 pilots are selected", async () => {
    renderComparison();
    const pilots = await api.listPilots();
    for (let i = 0; i < 5; i++) {
      const cb = await screen.findByTestId(`comparison-checkbox-${pilots[i].id}`);
      fireEvent.click(cb);
    }
    const dropdown = (await screen.findByTestId("comparison-quick-add")) as HTMLSelectElement;
    expect(dropdown.disabled).toBe(true);
  });

  // T4.1 (HONESTY): the metrics table must pair every pilot's numbers with
  // its actual PBO/DSR/Sharpe/MaxDD gate verdict -- a failing strategy's
  // equity curve/metrics must never get the same visual treatment as a
  // passing one with no indication it failed the gate.
  it("shows the Deployable gate badge for both a passing and a failing pilot in the metrics table", async () => {
    renderComparison();
    // trend-following: deployable=true (mock.ts). momentum-burst: deployable=false,
    // "Fails the overfitting gate" per its own mock.ts description.
    fireEvent.click(await screen.findByTestId("comparison-checkbox-trend-following"));
    fireEvent.click(await screen.findByTestId("comparison-checkbox-momentum-burst"));

    expect(await screen.findByText("Key Metrics Comparison")).toBeInTheDocument();
    const deployableRow = screen.getByText("Deployable").closest('[role="row"]') as HTMLElement;
    expect(within(deployableRow).getByText("● Deployable")).toBeInTheDocument();
    expect(within(deployableRow).getByText("▲ Not deployable")).toBeInTheDocument();
  });

  // T3.4 (regression): the Max Drawdown heatmap must highlight the SMALLER
  // (better) magnitude, never the larger one -- max_drawdown is a positive
  // fraction (0.19 = 19% drawdown), so "best" is a min, not a max. Fixture:
  // trend-following = 0.19, dip-buyer = 0.14 (dip-buyer is the honest best).
  it("highlights the smaller (better) Max Drawdown value, never the larger one", async () => {
    renderComparison();
    fireEvent.click(await screen.findByTestId("comparison-checkbox-trend-following"));
    fireEvent.click(await screen.findByTestId("comparison-checkbox-dip-buyer"));

    expect(await screen.findByText("Key Metrics Comparison")).toBeInTheDocument();
    const worseCell = screen.getByText("19%").closest('[role="cell"]') as HTMLElement;
    const betterCell = screen.getByText("14%").closest('[role="cell"]') as HTMLElement;

    expect(betterCell).toHaveStyle({ color: "rgb(16, 185, 129)" });
    expect(worseCell).not.toHaveStyle({ color: "rgb(16, 185, 129)" });
  });
});

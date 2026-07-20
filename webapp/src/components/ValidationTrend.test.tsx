/**
 * ValidationTrend.test.tsx — the cross-strategy validation snapshot + trend +
 * macro-regime timeline card (Strategy Health screen, below the per-Pilot
 * cards). Covers the three independently-degrading sections: the
 * all-strategies table (including a strategy with no Pilot mapping — the
 * whole reason this component exists), the metric-selectable trend chart,
 * and the regime-transition timeline, plus each section's honest empty state.
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ValidationTrend } from "./ValidationTrend";
import { api } from "../api/client";
import type { ValidationTrendSnapshot } from "../api/types";

afterEach(() => vi.restoreAllMocks());

describe("ValidationTrend (real mock API)", () => {
  it("renders every validated strategy, including one with no Pilot mapping", async () => {
    render(<ValidationTrend />);
    expect(await screen.findByTestId("validation-trend-row-multifactor_lowvol_size")).toBeInTheDocument();
    expect(await screen.findByTestId("validation-trend-row-timeseries_momentum")).toBeInTheDocument();
  });

  it("marks non-options-selling strategies' stress gate as n/a, never a fabricated pass", async () => {
    render(<ValidationTrend />);
    const row = await screen.findByTestId("validation-trend-row-timeseries_momentum");
    expect(row.textContent).toContain("n/a");
  });

  it("renders a failed stress gate for an options-selling strategy honestly", async () => {
    render(<ValidationTrend />);
    const row = await screen.findByTestId("validation-trend-row-short_vol_condor_pit");
    expect(row.textContent).toContain("✗ failed");
  });

  it("renders the metric-selectable trend chart and switches series on selection", async () => {
    render(<ValidationTrend />);
    expect(await screen.findByTestId("validation-trend-chart")).toBeInTheDocument();
    const select = await screen.findByTestId("validation-trend-metric-select");
    expect((select as HTMLSelectElement).value).toBe("dsr");
    fireEvent.change(select, { target: { value: "pbo" } });
    expect((select as HTMLSelectElement).value).toBe("pbo");
  });

  it("renders the macro regime timeline with only genuine transitions", async () => {
    render(<ValidationTrend />);
    const list = await screen.findByTestId("validation-trend-regime-list");
    expect(list.textContent).toContain("RISK ON");
    expect(list.textContent).toContain("RISK OFF");
    expect(list.textContent).toContain("NEUTRAL");
  });

  it("shows the honest cold-start reason when no strategies have been validated yet", async () => {
    vi.spyOn(api, "getValidationTrend").mockResolvedValueOnce({
      strategies: [],
      strategies_reason: "No reports/*_validation_summary.json files found yet.",
      trend: {},
      trend_reason: "No run-over-run history yet.",
      regime_timeline: [],
      n_rotated_snapshots: 0,
      regime_reason: "Regime timeline needs >= 2 rotated snapshots.",
    } satisfies ValidationTrendSnapshot);
    render(<ValidationTrend />);
    expect(await screen.findByTestId("validation-trend-strategies-empty")).toHaveTextContent(
      "No reports/*_validation_summary.json files found yet."
    );
    expect(screen.getByTestId("validation-trend-chart-empty")).toHaveTextContent(
      "No run-over-run history yet."
    );
    expect(screen.getByTestId("validation-trend-regime-empty")).toHaveTextContent(
      "Regime timeline needs >= 2 rotated snapshots."
    );
    // No metric selector when there's nothing to plot.
    expect(screen.queryByTestId("validation-trend-metric-select")).not.toBeInTheDocument();
  });

  it("a strategy row with null gate values renders '—', never a fabricated number", async () => {
    vi.spyOn(api, "getValidationTrend").mockResolvedValueOnce({
      strategies: [
        {
          strategy_id: "partial_strategy",
          deployable: null,
          pbo: null,
          dsr: 0.9,
          sharpe: null,
          max_drawdown: null,
          is_options_selling: false,
          stress_gate_passed: null,
          report_date: null,
        },
      ],
      strategies_reason: null,
      trend: {},
      trend_reason: "No run-over-run history yet.",
      regime_timeline: [],
      n_rotated_snapshots: 0,
      regime_reason: "Regime timeline needs >= 2 rotated snapshots.",
    } satisfies ValidationTrendSnapshot);
    render(<ValidationTrend />);
    const row = await screen.findByTestId("validation-trend-row-partial_strategy");
    expect(row.textContent).toContain("—");
    expect(row.querySelector(".badge")?.textContent).toContain("Not deployable"); // null -> not-deployable badge styling
  });

  it("surfaces an honest error state when the fetch fails", async () => {
    vi.spyOn(api, "getValidationTrend").mockRejectedValueOnce(new Error("boom"));
    render(<ValidationTrend />);
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });
});

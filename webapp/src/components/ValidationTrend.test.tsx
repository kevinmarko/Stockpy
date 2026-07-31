/**
 * ValidationTrend.test.tsx — the cross-strategy validation snapshot + trend
 * card (Strategy Health screen, below the per-Pilot cards). Covers the two
 * independently-degrading sections: the all-strategies table (including a
 * strategy with no Pilot mapping — the whole reason this component exists)
 * and the metric-selectable trend chart, plus each section's honest empty
 * state. (The `GET /strategy/validation-trend` payload also carries a
 * macro-regime transition timeline; this component deliberately doesn't
 * render it — see ValidationTrend.tsx's top-of-file comment — so the mock
 * payloads below still populate those fields to satisfy the API contract
 * type without asserting on them.)
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

  it("does not render a macro regime timeline section", async () => {
    render(<ValidationTrend />);
    expect(await screen.findByTestId("validation-trend-chart")).toBeInTheDocument();
    expect(screen.queryByTestId("validation-trend-regime")).not.toBeInTheDocument();
    expect(screen.queryByText("Macro regime timeline")).not.toBeInTheDocument();
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

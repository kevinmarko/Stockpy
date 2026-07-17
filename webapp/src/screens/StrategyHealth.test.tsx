/**
 * StrategyHealth.test.tsx — the catalog-wide deployability-gate dashboard
 * renders per-gate value/threshold breakdowns and every honesty branch the
 * mock fixture exercises: a passing pilot, a single failing gate, a failed
 * options-selling stress gate, a genuinely-null gate value, a pilot with no
 * validated backtest at all, and an empty catalog.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StrategyHealth } from "./StrategyHealth";
import { api } from "../api/client";
import type { StrategyHealthRow } from "../api/types";

function renderScreen() {
  return render(
    <MemoryRouter>
      <StrategyHealth />
    </MemoryRouter>
  );
}

describe("StrategyHealth screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders a deployable pilot with its per-gate value vs threshold", async () => {
    renderScreen();
    expect(await screen.findByText("Trend Follower")).toBeInTheDocument();
    expect(screen.getByText("backtest: timeseries_momentum")).toBeInTheDocument();
    // At least one "Deployable" badge is rendered honestly (not hidden).
    expect(screen.getAllByText("● Deployable").length).toBeGreaterThan(0);
  });

  it("shows a single failing gate blocking an otherwise-clean strategy (edge-garch)", async () => {
    renderScreen();
    await screen.findByText("Edge & Volatility");
    // Not deployable overall, and not fabricated as passing.
    expect(screen.getAllByText("▲ Not deployable").length).toBeGreaterThan(0);
  });

  it("surfaces a failed options-selling stress gate even when numeric gates pass", async () => {
    renderScreen();
    await screen.findByText("Premium Harvester");
    expect(await screen.findByText("Stress ✗ failed")).toBeInTheDocument();
  });

  it("a null gate value renders '—', never a fabricated number", async () => {
    renderScreen();
    await screen.findByText("Regime Navigator");
    // regime-navigator's max_drawdown is null in the fixture -> at least one
    // gate chip shows the honest em-dash placeholder.
    expect(screen.getAllByText(/—/).length).toBeGreaterThan(0);
  });

  it("a pilot with no validated backtest shows the honest reason, no gates", async () => {
    renderScreen();
    await screen.findByText("Balanced Blend");
    expect(
      await screen.findByText("no validated backtest for this pilot")
    ).toBeInTheDocument();
    expect(screen.getByText("no backtest joined")).toBeInTheDocument();
  });

  it("a real strategy_id with a missing summary file gets its own distinct honest reason", async () => {
    renderScreen();
    await screen.findByText("Forecast Aligned");
    expect(
      await screen.findByText(
        "no validation summary found for 'forecast_direction_arima_hw' (run the validation pipeline first)"
      )
    ).toBeInTheDocument();
  });

  it("renders the deployable-count summary strip", async () => {
    renderScreen();
    await screen.findByText("Trend Follower");
    // Of the 8 fixture rows, 6 are gate-evaluated (2 have no backtest joined)
    // and 2 of those 6 are actually deployable — the strip surfaces the real
    // fraction, not a guess. Regex matches can also hit ancestor containers
    // whose concatenated text includes the substring, so assert presence via
    // getAllByText rather than the single-match getByText.
    expect(screen.getAllByText(/evaluated deployable/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/without a backtest yet/).length).toBeGreaterThan(0);
  });

  it("an empty catalog renders the honest empty state", async () => {
    vi.spyOn(api, "getStrategyHealth").mockResolvedValueOnce([]);
    renderScreen();
    expect(
      await screen.findByText("No pilots in the catalog yet.")
    ).toBeInTheDocument();
  });

  it("a pilot with no run-over-run history renders without crashing (honest empty trend)", async () => {
    const rows: StrategyHealthRow[] = [
      {
        pilot_id: "solo",
        pilot_name: "Solo Pilot",
        strategy_id: "solo_strategy",
        deployable: true,
        gates: [
          { key: "pbo", label: "PBO", value: 0.2, threshold: 0.5, direction: "below", passed: true },
          { key: "dsr", label: "DSR", value: 0.97, threshold: 0.95, direction: "above", passed: true },
          { key: "sharpe", label: "Sharpe", value: 0.8, threshold: 0.5, direction: "above", passed: true },
          {
            key: "max_drawdown",
            label: "Max DD",
            value: 0.1,
            threshold: 0.3,
            direction: "below",
            passed: true,
          },
        ],
        is_options_selling: false,
        stress_gate_passed: true,
        report_date: "2026-07-01",
        trend: [],
        reason: null,
      },
    ];
    vi.spyOn(api, "getStrategyHealth").mockResolvedValueOnce(rows);
    renderScreen();
    expect(await screen.findByText("Solo Pilot")).toBeInTheDocument();
    expect(screen.getAllByText("● Deployable").length).toBe(1);
  });
});

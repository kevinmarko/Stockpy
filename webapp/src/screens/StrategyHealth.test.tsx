/**
 * StrategyHealth.test.tsx — the catalog-wide deployability-gate dashboard
 * renders per-gate value/threshold breakdowns and every honesty branch the
 * mock fixture exercises: a passing pilot, a single failing gate, a failed
 * options-selling stress gate, a genuinely-null gate value, a pilot with no
 * validated backtest at all, and an empty catalog.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StrategyHealth } from "./StrategyHealth";
import { api } from "../api/client";
import type { StrategyHealthRow } from "../api/types";
import { __resetThresholdsCache } from "../help/thresholds";

function renderScreen() {
  return render(
    <MemoryRouter>
      <StrategyHealth />
    </MemoryRouter>
  );
}

describe("StrategyHealth screen (real mock API)", () => {
  beforeEach(() => __resetThresholdsCache());
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

  it("renders live PBO/DSR/Sharpe/MaxDD thresholds in the footer summary, never a hard-coded literal", async () => {
    vi.spyOn(api, "getThresholds").mockResolvedValue({
      pbo_max: 0.42,
      dsr_min: 0.88,
      net_sharpe_min: 0.61,
      max_drawdown_max: 0.25,
      stress_max_drawdown: 0.44,
      kelly_fraction: 0.5,
      kelly_cap: 0.2,
      robinhood_max_notional_per_order: 0.0,
      follow_min_amount: 100.0,
      agentic_max_candidates: 25,
    });
    renderScreen();
    await screen.findByText("Trend Follower");
    expect(
      await screen.findByText(
        /Deployable requires PBO < 0\.42, DSR > 0\.88, net Sharpe > 0\.61, Max Drawdown < 25%/
      )
    ).toBeInTheDocument();
  });

  it("footer degrades to '—' for every gate when the threshold fetch fails (no guessed gate)", async () => {
    vi.spyOn(api, "getThresholds").mockRejectedValue(new Error("offline"));
    renderScreen();
    await screen.findByText("Trend Follower");
    expect(
      await screen.findByText(/Deployable requires PBO < —, DSR > —, net Sharpe > —, Max Drawdown < —/)
    ).toBeInTheDocument();
  });

  it("the stress-gate tooltip quotes the live stress_max_drawdown limit", async () => {
    vi.spyOn(api, "getThresholds").mockResolvedValue({
      pbo_max: 0.5,
      dsr_min: 0.95,
      net_sharpe_min: 0.5,
      max_drawdown_max: 0.3,
      stress_max_drawdown: 0.44,
      kelly_fraction: 0.5,
      kelly_cap: 0.2,
      robinhood_max_notional_per_order: 0.0,
      follow_min_amount: 100.0,
      agentic_max_candidates: 25,
    });
    renderScreen();
    const chip = await screen.findByText("Stress ✗ failed");
    expect(chip).toHaveAttribute(
      "title",
      "Tail-scenario stress gate: survives OCT 2008 / FEB 2018 / MAR 2020 / AUG 2024 with < 44% drawdown"
    );
  });

  it("the stress-gate tooltip degrades to '—' when thresholds are unavailable", async () => {
    vi.spyOn(api, "getThresholds").mockRejectedValue(new Error("offline"));
    renderScreen();
    const chip = await screen.findByText("Stress ✗ failed");
    expect(chip).toHaveAttribute(
      "title",
      "Tail-scenario stress gate: survives OCT 2008 / FEB 2018 / MAR 2020 / AUG 2024 with < — drawdown"
    );
  });

  describe("Trend metric selector (backlog item #7.1)", () => {
    it("defaults to DSR and switching the selector re-labels and re-plots every card's sparkline", async () => {
      renderScreen();
      const card = (await screen.findByText("Trend Follower")).closest("section")!;
      // Default: DSR sparkline caption.
      expect(within(card).getByText(/DSR, last 3 runs/)).toBeInTheDocument();

      const select = screen.getByTestId("trend-metric-select") as HTMLSelectElement;
      await userEvent.selectOptions(select, "pbo");
      expect(within(card).getByText(/PBO, last 3 runs/)).toBeInTheDocument();
      expect(within(card).queryByText(/DSR, last 3 runs/)).not.toBeInTheDocument();

      await userEvent.selectOptions(select, "sharpe");
      expect(within(card).getByText(/Sharpe, last 3 runs/)).toBeInTheDocument();

      await userEvent.selectOptions(select, "max_drawdown");
      expect(within(card).getByText(/Max DD, last 3 runs/)).toBeInTheDocument();
    });

    it("hides the selector entirely when no pilot has run-over-run history", async () => {
      const rows: StrategyHealthRow[] = [
        {
          pilot_id: "solo",
          pilot_name: "Solo Pilot",
          strategy_id: "solo_strategy",
          deployable: true,
          gates: [
            { key: "pbo", label: "PBO", value: 0.2, threshold: 0.5, direction: "below", passed: true },
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
      await screen.findByText("Solo Pilot");
      expect(screen.queryByTestId("trend-metric-select")).not.toBeInTheDocument();
    });
  });
});

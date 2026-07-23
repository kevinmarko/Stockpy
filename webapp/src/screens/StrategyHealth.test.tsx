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
import type { GravityAuditStatus, StrategyHealthRow } from "../api/types";
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
      retrain_window_days: 30,
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
      retrain_window_days: 30,
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

  it("renders the cross-strategy validation section below the per-pilot cards, including a strategy not wired to any Pilot", async () => {
    renderScreen();
    await screen.findByText("Trend Follower");
    expect(await screen.findByText("Cross-strategy validation")).toBeInTheDocument();
    // multifactor_lowvol_size has no pilots.catalog Pilot pointing at it --
    // invisible above in the per-pilot cards, but must appear here.
    expect(await screen.findByTestId("validation-trend-row-multifactor_lowvol_size")).toBeInTheDocument();
  });

  it("does not render the cross-strategy section when the catalog itself is empty", async () => {
    vi.spyOn(api, "getStrategyHealth").mockResolvedValueOnce([]);
    renderScreen();
    await screen.findByText("No pilots in the catalog yet.");
    expect(screen.queryByText("Cross-strategy validation")).not.toBeInTheDocument();
  });
});

/**
 * Gravity Audit section — read-only port of gui/panels/gravity_audit.py's AI
 * Gravity audit runner + legacy structural Gravity Review Suite. No trigger
 * exists on this screen for either audit (a deliberate scope cut); these
 * tests only cover the read/render paths.
 */
describe("StrategyHealth screen — Gravity Audit section (real mock API)", () => {
  beforeEach(() => __resetThresholdsCache());
  afterEach(() => vi.restoreAllMocks());

  it("renders the mock fixture's ready AI audit with a real Claude/Gemini disagreement", async () => {
    renderScreen();
    expect(await screen.findByText("AI Gravity Audit (Claude + Gemini)")).toBeInTheDocument();
    expect(screen.getByText("ready")).toBeInTheDocument();
    expect(
      screen.getByText(/1 model disagreement\(s\); Claude skipped=0 \/ Gemini skipped=0/)
    ).toBeInTheDocument();
    expect(screen.getByText("8 steps")).toBeInTheDocument();
    expect(screen.getByText("Claude 8✓ / 0✗")).toBeInTheDocument();
    expect(screen.getByText("Gemini 7✓ / 1✗")).toBeInTheDocument();
    expect(screen.getByText("1 disagreement(s)")).toBeInTheDocument();
    expect(screen.getByText(/Options Pricing Engine/)).toBeInTheDocument();
    expect(screen.getByText("⚠ disagree")).toBeInTheDocument();
  });

  it("renders the mock fixture's legacy audit with one genuinely failing step", async () => {
    renderScreen();
    expect(await screen.findByText("Legacy Structural Audit")).toBeInTheDocument();
    expect(
      screen.getByText("❌ At least one step failed on the last run — not cleared for live.")
    ).toBeInTheDocument();
    expect(screen.getByText("step_4_signal_registry_health")).toBeInTheDocument();
  });

  it("disabled AI runner status renders the .env hint, not a fabricated audit", async () => {
    const disabled: GravityAuditStatus = {
      ai_audit: {
        status: "disabled",
        enabled: false,
        generated_at: null,
        health: "empty",
        health_caption: "No AI Gravity audit run yet.",
        total_steps: 0,
        claude_passed: 0,
        claude_failed: 0,
        claude_skipped: 0,
        gemini_passed: 0,
        gemini_failed: 0,
        gemini_skipped: 0,
        disagreements: 0,
        steps: [],
      },
      legacy_audit: {
        available: false,
        all_passed: null,
        steps: [],
        reason: "No Gravity Review Suite run recorded yet — launch it from the desktop Command Center's Safety tab.",
      },
    };
    vi.spyOn(api, "getGravityAuditStatus").mockResolvedValueOnce(disabled);
    renderScreen();
    expect(
      await screen.findByText(/AI Gravity runner is off\. Set GRAVITY_AI_RUNNER_ENABLED=true/)
    ).toBeInTheDocument();
    expect(screen.getByText("No AI Gravity audit run yet.")).toBeInTheDocument();
    // No steps table, no fabricated KPI strip, when nothing has run yet.
    expect(screen.queryByText(/steps$/)).not.toBeInTheDocument();
    expect(
      await screen.findByText(/No Gravity Review Suite run recorded yet/)
    ).toBeInTheDocument();
  });

  it("a clean legacy audit (all steps passed) renders the green banner honestly", async () => {
    const clean: GravityAuditStatus = {
      ai_audit: {
        status: "disabled",
        enabled: false,
        generated_at: null,
        health: "empty",
        health_caption: "No AI Gravity audit run yet.",
        total_steps: 0,
        claude_passed: 0,
        claude_failed: 0,
        claude_skipped: 0,
        gemini_passed: 0,
        gemini_failed: 0,
        gemini_skipped: 0,
        disagreements: 0,
        steps: [],
      },
      legacy_audit: {
        available: true,
        all_passed: true,
        steps: [{ step: "step_1_schema", passed: true, status: "PASSED" }],
        reason: null,
      },
    };
    vi.spyOn(api, "getGravityAuditStatus").mockResolvedValueOnce(clean);
    renderScreen();
    expect(
      await screen.findByText("✅ All steps passed on the last run.")
    ).toBeInTheDocument();
  });

  it("never 500s the screen when the endpoint errors -- shows ErrorState with retry", async () => {
    vi.spyOn(api, "getGravityAuditStatus").mockRejectedValueOnce(new Error("offline"));
    renderScreen();
    await screen.findByText("Trend Follower"); // pilot cards still render independently
    expect(await screen.findByText(/offline/)).toBeInTheDocument();
  });
});

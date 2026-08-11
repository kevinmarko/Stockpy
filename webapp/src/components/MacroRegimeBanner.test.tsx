/**
 * MacroRegimeBanner.test.tsx
 *
 * Covers happy-path render, loading, error, and the real cold-start empty
 * state (a state snapshot that hasn't been written yet -- `regime.reason`
 * non-null, CONSTRAINT #4: never a fabricated 0/placeholder in its place).
 * Also pins the "both must be true" contract from `RegimeOverlay.macro_kill_switch`'s
 * own doc comment: the warn Notice only renders when `macro_kill_switch` AND
 * `macro_regime_gate_enabled` are BOTH `true` -- never conflating that with
 * the unrelated `kill_switch_active` (manual global kill-switch file) field.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MacroRegimeBanner } from "./MacroRegimeBanner";
import { api, ApiError } from "../api/client";
import type { ObservabilitySummary, RegimeOverlay } from "../api/types";
import {
  mockEtfTransmissionDisabled,
  mockForecastSkillBySymbolEmpty,
  mockHeartbeatNoData,
  mockLatencyHeatmapDisabled,
  mockSizingCapAuditDisabled,
  mockStrategyPnlEmpty,
  mockSystemTelemetryUnavailable,
} from "../api/mock";

const BASE_REGIME: RegimeOverlay = {
  as_of: "2026-08-11T10:00:00+00:00",
  market_regime: "RISK ON",
  vix: 14.8,
  sahm_rule: 0.13,
  high_yield_oas: 3.21,
  yield_curve: 0.42,
  hmm_risk_on_probability: 0.78,
  kill_switch_active: false,
  macro_regime_gate_enabled: true,
  macro_kill_switch: false,
  reason: null,
  macro_gate_writable: true,
  macro_gate_writable_note: "",
};

function buildSummary(regime: RegimeOverlay): ObservabilitySummary {
  return {
    portfolio_risk: {
      sharpe_ratio: null, calmar_ratio: null, max_drawdown: null,
      max_drawdown_duration_days: null, cagr: null, n_snapshots: 0,
      min_snapshots_required: 20, reason: "No account snapshots yet.",
    },
    portfolio_heat: {
      heat_pct: null, max_portfolio_heat: 0.06, over_limit: null,
      n_positions: 0, as_of: null, reason: "No account snapshot yet.",
    },
    equity_curve: { range: "1M", points: [], reason: "No account snapshots yet." },
    regime,
    forecast_skill: {
      horizon_days: 30, window_days: 180, min_obs: 30, reliability_curve: [],
      skill_weights: {}, pending: 0, completed: 0, reason: "No forecast history yet.",
    },
    forecast_skill_by_symbol: mockForecastSkillBySymbolEmpty(),
    risk_gate_blocks: { entries: [], count: 0, reason: "No risk-gate blocks logged yet." },
    latency_heatmap: mockLatencyHeatmapDisabled(),
    circuit_breakers: {
      trips: [], counts: { critical: 0, warning: 0, total: 0 }, window_hours: 24,
      reason: "No active circuit-breaker trips.",
    },
    system_telemetry: mockSystemTelemetryUnavailable(),
    sizing_cap_audit: mockSizingCapAuditDisabled(),
    etf_transmission: mockEtfTransmissionDisabled(),
    heartbeat: mockHeartbeatNoData(),
    strategy_pnl: mockStrategyPnlEmpty(),
  };
}

describe("MacroRegimeBanner", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders market regime + VIX on the happy path, no warn notice when the macro kill switch is quiet", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValue(buildSummary(BASE_REGIME));
    render(<MacroRegimeBanner />);

    const widget = await screen.findByTestId("macroRegime-widget");
    expect(widget).toHaveTextContent("RISK ON");
    expect(widget).toHaveTextContent("14.8");
    expect(screen.queryByTestId("macroRegime-kill-switch-notice")).not.toBeInTheDocument();
  });

  it("shows a real loading state before the fetch resolves", () => {
    vi.spyOn(api, "getObservabilitySummary").mockImplementation(() => new Promise(() => {}));
    render(<MacroRegimeBanner />);
    expect(screen.queryByTestId("macroRegime-widget")).not.toBeInTheDocument();
    expect(document.querySelector(".skeleton")).toBeInTheDocument();
  });

  it("shows a real error state (not a blank widget) when the fetch fails", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockRejectedValue(
      new ApiError("summary fetch failed", 500)
    );
    render(<MacroRegimeBanner />);
    expect(await screen.findByText("summary fetch failed")).toBeInTheDocument();
    expect(screen.queryByTestId("macroRegime-widget")).not.toBeInTheDocument();
  });

  it("renders the honest cold-start empty state, never a fabricated 0/placeholder, when no state snapshot exists yet", async () => {
    const coldStart: RegimeOverlay = {
      as_of: null, market_regime: null, vix: null, sahm_rule: null,
      high_yield_oas: null, yield_curve: null, hmm_risk_on_probability: null,
      kill_switch_active: null, macro_regime_gate_enabled: null,
      macro_kill_switch: null, reason: "No state snapshot yet.",
      macro_gate_writable: false, macro_gate_writable_note: "Writes are disabled.",
    };
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValue(buildSummary(coldStart));
    render(<MacroRegimeBanner />);

    expect(await screen.findByText("No state snapshot yet.")).toBeInTheDocument();
    expect(screen.queryByTestId("macroRegime-widget")).not.toBeInTheDocument();
    // Never a fabricated "0.0" VIX or "UNKNOWN" regime standing in for the gap.
    expect(screen.queryByText("0.0")).not.toBeInTheDocument();
  });

  it("shows the warn notice only when BOTH macro_kill_switch AND macro_regime_gate_enabled are true (never conflated with kill_switch_active)", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValue(
      buildSummary({
        ...BASE_REGIME,
        macro_kill_switch: true,
        macro_regime_gate_enabled: true,
        kill_switch_active: false, // the unrelated manual switch stays off
      })
    );
    render(<MacroRegimeBanner />);

    const notice = await screen.findByTestId("macroRegime-kill-switch-notice");
    expect(notice).toHaveTextContent("Macro kill switch active");
  });

  it("does NOT show the warn notice when macro_kill_switch is true but the gate is disabled (hybrid mode)", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValue(
      buildSummary({
        ...BASE_REGIME,
        macro_kill_switch: true,
        macro_regime_gate_enabled: false,
      })
    );
    render(<MacroRegimeBanner />);

    await screen.findByTestId("macroRegime-widget");
    expect(screen.queryByTestId("macroRegime-kill-switch-notice")).not.toBeInTheDocument();
  });
});

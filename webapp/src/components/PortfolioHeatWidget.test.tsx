/**
 * PortfolioHeatWidget.test.tsx
 *
 * Covers happy-path render (bar + raw numbers), loading, error, and the real
 * empty/cold-start state (heat_pct == null -> honest reason, NEVER a
 * fabricated 0% -- CONSTRAINT #4), plus the over-limit red-bar branch.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PortfolioHeatWidget } from "./PortfolioHeatWidget";
import { api, ApiError } from "../api/client";
import {
  mockEtfTransmissionDisabled,
  mockForecastSkillBySymbolEmpty,
  mockHeartbeatNoData,
  mockLatencyHeatmapDisabled,
  mockSizingCapAuditDisabled,
  mockStrategyPnlEmpty,
  mockSystemTelemetryUnavailable,
} from "../api/mock";
import type { ObservabilitySummary } from "../api/types";

/** Every field beyond `portfolio_heat` is irrelevant to this widget --
 * filled with the same cold-start/empty shapes Observability.test.tsx uses
 * so this stays a valid ObservabilitySummary without re-deriving each
 * nested contract by hand. */
function baseSummary(): Omit<ObservabilitySummary, "portfolio_heat"> {
  return {
    portfolio_risk: {
      sharpe_ratio: null,
      calmar_ratio: null,
      max_drawdown: null,
      max_drawdown_duration_days: null,
      cagr: null,
      n_snapshots: 0,
      min_snapshots_required: 20,
      reason: "No account snapshots yet.",
    },
    equity_curve: { range: "1Y", points: [], reason: "No account snapshots yet." },
    regime: {
      as_of: null,
      market_regime: null,
      vix: null,
      sahm_rule: null,
      high_yield_oas: null,
      yield_curve: null,
      hmm_risk_on_probability: null,
      kill_switch_active: null,
      macro_regime_gate_enabled: null,
      macro_kill_switch: null,
      reason: "No state snapshot yet.",
      macro_gate_writable: false,
      macro_gate_writable_note: "Writes are disabled (MACRO_GATE_WRITES_ENABLED=false).",
    },
    forecast_skill: {
      horizon_days: 30,
      window_days: 180,
      min_obs: 30,
      reliability_curve: [],
      skill_weights: {},
      pending: 0,
      completed: 0,
      reason: "No forecast history yet.",
    },
    forecast_skill_by_symbol: mockForecastSkillBySymbolEmpty(),
    risk_gate_blocks: { entries: [], count: 0, reason: "No risk-gate blocks logged yet." },
    latency_heatmap: mockLatencyHeatmapDisabled(),
    circuit_breakers: {
      trips: [],
      counts: { critical: 0, warning: 0, total: 0 },
      window_hours: 24,
      reason: "No active circuit-breaker trips in the last 24h.",
    },
    system_telemetry: mockSystemTelemetryUnavailable(),
    sizing_cap_audit: mockSizingCapAuditDisabled(),
    etf_transmission: mockEtfTransmissionDisabled(),
    heartbeat: mockHeartbeatNoData(),
    strategy_pnl: mockStrategyPnlEmpty(),
  };
}

describe("PortfolioHeatWidget", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the bar and raw heat_pct/max_portfolio_heat numbers on the happy path", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValue({
      ...baseSummary(),
      portfolio_heat: {
        heat_pct: 0.021,
        max_portfolio_heat: 0.06,
        over_limit: false,
        n_positions: 4,
        as_of: new Date().toISOString(),
        reason: null,
      },
    });

    render(<PortfolioHeatWidget />);

    const widget = await screen.findByTestId("portfolioHeat-widget");
    expect(widget).toBeInTheDocument();
    expect(screen.getByText("2.1%")).toBeInTheDocument();
    expect(screen.getByText("cap 6%")).toBeInTheDocument();
    expect(screen.queryByText(/Over the configured portfolio heat limit/)).not.toBeInTheDocument();
  });

  it("renders the over-limit warning when over_limit is true", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValue({
      ...baseSummary(),
      portfolio_heat: {
        heat_pct: 0.072,
        max_portfolio_heat: 0.06,
        over_limit: true,
        n_positions: 6,
        as_of: new Date().toISOString(),
        reason: null,
      },
    });

    render(<PortfolioHeatWidget />);

    expect(await screen.findByText("7.2%")).toBeInTheDocument();
    expect(
      await screen.findByText("Over the configured portfolio heat limit.")
    ).toBeInTheDocument();
  });

  it("shows a loading state before the fetch resolves", async () => {
    let resolveFn: (v: ObservabilitySummary) => void = () => {};
    vi.spyOn(api, "getObservabilitySummary").mockReturnValue(
      new Promise((resolve) => {
        resolveFn = resolve;
      })
    );

    render(<PortfolioHeatWidget />);
    expect(document.querySelector(".skeleton")).not.toBeNull();
    expect(screen.queryByTestId("portfolioHeat-widget")).not.toBeInTheDocument();

    resolveFn({
      ...baseSummary(),
      portfolio_heat: {
        heat_pct: 0.02,
        max_portfolio_heat: 0.06,
        over_limit: false,
        n_positions: 3,
        as_of: new Date().toISOString(),
        reason: null,
      },
    });
    expect(await screen.findByTestId("portfolioHeat-widget")).toBeInTheDocument();
  });

  it("renders an honest error state (never fabricated data) on a hard failure", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockRejectedValue(
      new ApiError("Network error", 500)
    );

    render(<PortfolioHeatWidget />);

    expect(await screen.findByText("Couldn't load")).toBeInTheDocument();
    expect(screen.queryByTestId("portfolioHeat-widget")).not.toBeInTheDocument();
  });

  it("renders the honest cold-start reason (never a fabricated 0%) when heat_pct is null", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValue({
      ...baseSummary(),
      portfolio_heat: {
        heat_pct: null,
        max_portfolio_heat: 0.06,
        over_limit: null,
        n_positions: 0,
        as_of: null,
        reason: "No account snapshot yet — run `python3 main.py --refresh-account` to populate.",
      },
    });

    render(<PortfolioHeatWidget />);

    expect(await screen.findByText("No portfolio heat reading yet")).toBeInTheDocument();
    expect(
      await screen.findByText(
        /No account snapshot yet — run `python3 main.py --refresh-account` to populate\./
      )
    ).toBeInTheDocument();
    expect(screen.queryByTestId("portfolioHeat-widget")).not.toBeInTheDocument();
    expect(screen.queryByText("0.0%")).not.toBeInTheDocument();
  });
});

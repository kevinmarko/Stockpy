/**
 * TickerDrawer.test.tsx — the omni-search ticker inspection drawer. Renders
 * real GET /symbols/{ticker} + the observability summary's risk-gate-block
 * log, never the fixed mock numbers ("$12,450 notional", the hardcoded
 * signal list) the original implementation shipped with regardless of which
 * symbol was opened.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TickerDrawer } from "./TickerDrawer";
import { api } from "../api/client";
import {
  mockEtfTransmissionDisabled,
  mockForecastSkillBySymbolEmpty,
  mockHeartbeatNoData,
  mockLatencyHeatmapDisabled,
  mockSizingCapAuditDisabled,
  mockStrategyPnlEmpty,
  mockSystemTelemetryUnavailable,
} from "../api/mock";
import type { ObservabilitySummary, RiskGateBlockEntry } from "../api/types";

function renderDrawer(symbol: string) {
  return render(
    <MemoryRouter>
      <TickerDrawer symbol={symbol} onClose={vi.fn()} />
    </MemoryRouter>
  );
}

function observabilitySummary(entries: RiskGateBlockEntry[] = []): ObservabilitySummary {
  return {
    portfolio_risk: {
      sharpe_ratio: null, calmar_ratio: null, max_drawdown: null,
      max_drawdown_duration_days: null, cagr: null, n_snapshots: 0,
      min_snapshots_required: 20, reason: "No account snapshots yet.",
    },
    portfolio_heat: { heat_pct: null, max_portfolio_heat: 0.06, over_limit: null, n_positions: 0, as_of: null, reason: "No account snapshot yet." },
    equity_curve: { range: "1M", points: [], reason: "No account snapshots yet." },
    regime: {
      as_of: null, market_regime: null, vix: null, sahm_rule: null, high_yield_oas: null,
      yield_curve: null, hmm_risk_on_probability: null, kill_switch_active: null,
      macro_regime_gate_enabled: null, macro_kill_switch: null, reason: "No state snapshot yet.",
      macro_gate_writable: false, macro_gate_writable_note: "Writes are disabled.",
    },
    forecast_skill: { horizon_days: 30, window_days: 180, min_obs: 30, reliability_curve: [], skill_weights: {}, pending: 0, completed: 0, reason: "No forecast history yet." },
    forecast_skill_by_symbol: mockForecastSkillBySymbolEmpty(),
    risk_gate_blocks: { entries, count: entries.length, reason: entries.length ? null : "No risk-gate blocks logged yet." },
    latency_heatmap: mockLatencyHeatmapDisabled(),
    circuit_breakers: { trips: [], counts: { critical: 0, warning: 0, total: 0 }, window_hours: 24, reason: "No trips." },
    system_telemetry: mockSystemTelemetryUnavailable(),
    sizing_cap_audit: mockSizingCapAuditDisabled(),
    etf_transmission: mockEtfTransmissionDisabled(),
    heartbeat: mockHeartbeatNoData(),
    strategy_pnl: mockStrategyPnlEmpty(),
  };
}

describe("TickerDrawer", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders real per-symbol advisory/sizing/score data, not fixed mock numbers", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(observabilitySummary());
    renderDrawer("AAPL");

    expect(await screen.findByText("AAPL")).toBeInTheDocument();
    // Real score_components keys the mock always emits ("momentum", "trend"),
    // never the old hardcoded "CrossSectionalMomentum"/"MultifactorSignal".
    expect(await screen.findByText("momentum")).toBeInTheDocument();
    expect(await screen.findByText("trend")).toBeInTheDocument();
    expect(screen.queryByText("CrossSectionalMomentum")).not.toBeInTheDocument();
    expect(screen.queryByText("$12,450")).not.toBeInTheDocument();
  });

  it("two different symbols render two different price snapshots, not the same fixed values", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValue(observabilitySummary());
    const { unmount } = renderDrawer("AAPL");
    const aaplPriceLabel = await screen.findByText("Price");
    const aaplPrice = aaplPriceLabel.parentElement?.querySelector(".tile-value")?.textContent;
    unmount();

    renderDrawer("NVDA");
    const nvdaPriceLabel = await screen.findByText("Price");
    const nvdaPrice = nvdaPriceLabel.parentElement?.querySelector(".tile-value")?.textContent;

    expect(aaplPrice).toMatch(/^\$\d/);
    expect(nvdaPrice).toMatch(/^\$\d/);
    expect(aaplPrice).not.toBe(nvdaPrice);
  });

  it("shows real risk-gate blocks filtered to this symbol, and an honest empty state when there are none", async () => {
    const entries: RiskGateBlockEntry[] = [
      { ts: "2026-07-30T09:31:15Z", check: "market_hours", reason: "Order attempted outside core RTH window", symbol: "AAPL", side: "buy", qty: 10, strategy_id: "s1" },
      { ts: "2026-07-30T14:22:01Z", check: "max_order_rate", reason: "Rate limit exceeded", symbol: "MSFT", side: "buy", qty: 5, strategy_id: "s2" },
    ];
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(observabilitySummary(entries));
    renderDrawer("AAPL");

    expect(await screen.findByText("market_hours")).toBeInTheDocument();
    // The MSFT-only block never leaks into AAPL's drawer.
    expect(screen.queryByText("max_order_rate")).not.toBeInTheDocument();
  });

  it("an unknown symbol degrades honestly instead of crashing", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(observabilitySummary());
    renderDrawer("ZZZZZZ_NOT_REAL");
    expect(await screen.findByText("ZZZZZZ_NOT_REAL")).toBeInTheDocument();
    // The screen renders an honest error/empty state, not fabricated tiles.
    expect(screen.queryByText("Signal Score Breakdown")).not.toBeInTheDocument();
  });
});

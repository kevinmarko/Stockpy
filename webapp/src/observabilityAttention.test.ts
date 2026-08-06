/**
 * observabilityAttention.test.ts — pure-function coverage for
 * deriveAttentionItems, independent of any screen. Observability.test.tsx and
 * Dashboard.test.tsx separately confirm both screens actually call this and
 * render its output; this file pins the derivation logic itself.
 */
import { describe, expect, it } from "vitest";
import { deriveAttentionItems } from "./observabilityAttention";
import {
  mockEtfTransmissionDisabled,
  mockForecastSkillBySymbolEmpty,
  mockHeartbeatNoData,
  mockLatencyHeatmapDisabled,
  mockSizingCapAuditDisabled,
  mockStrategyPnlEmpty,
  mockSystemTelemetryUnavailable,
} from "./api/mock";
import type { ObservabilitySummary } from "./api/types";

/** A fully cold-start / all-clear summary — mirrors Observability.test.tsx's
 * COLD_START fixture exactly (kept independent rather than imported, since
 * that fixture lives in a .tsx test file). */
const ALL_CLEAR: ObservabilitySummary = {
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
  portfolio_heat: {
    heat_pct: null,
    max_portfolio_heat: 0.06,
    over_limit: null,
    n_positions: 0,
    as_of: null,
    reason: "No account snapshot yet.",
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
    macro_gate_writable_note: "Writes are disabled.",
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
    reason: "No active circuit-breaker trips.",
  },
  system_telemetry: mockSystemTelemetryUnavailable(),
  sizing_cap_audit: mockSizingCapAuditDisabled(),
  etf_transmission: mockEtfTransmissionDisabled(),
  heartbeat: mockHeartbeatNoData(),
  strategy_pnl: mockStrategyPnlEmpty(),
};

describe("deriveAttentionItems", () => {
  it("returns an empty list for an all-clear/cold-start summary — never a fabricated item", () => {
    expect(deriveAttentionItems(ALL_CLEAR)).toEqual([]);
  });

  it("flags critical and warning circuit-breaker counts as separate items", () => {
    const items = deriveAttentionItems({
      ...ALL_CLEAR,
      circuit_breakers: { ...ALL_CLEAR.circuit_breakers, counts: { critical: 2, warning: 1, total: 3 } },
    });
    expect(items.find((i) => i.id === "circuit-critical")).toMatchObject({
      severity: "critical",
      label: "2 critical circuit breakers tripped",
      anchor: "circuit-breakers",
    });
    expect(items.find((i) => i.id === "circuit-warning")).toMatchObject({
      severity: "warning",
      label: "1 circuit breaker warning",
      anchor: "circuit-breakers",
    });
  });

  it("flags the macro regime gate only when explicitly false, never when unknown (null)", () => {
    expect(
      deriveAttentionItems({
        ...ALL_CLEAR,
        regime: { ...ALL_CLEAR.regime, macro_regime_gate_enabled: false },
      }).find((i) => i.id === "macro-gate-off")
    ).toMatchObject({ severity: "warning", anchor: "macro-gate" });

    // null (unknown -- no state snapshot yet) must NOT be treated as "off".
    expect(
      deriveAttentionItems({
        ...ALL_CLEAR,
        regime: { ...ALL_CLEAR.regime, macro_regime_gate_enabled: null },
      }).find((i) => i.id === "macro-gate-off")
    ).toBeUndefined();

    // true (the normal, safe state) must not be flagged either.
    expect(
      deriveAttentionItems({
        ...ALL_CLEAR,
        regime: { ...ALL_CLEAR.regime, macro_regime_gate_enabled: true },
      }).find((i) => i.id === "macro-gate-off")
    ).toBeUndefined();
  });

  it("flags portfolio heat only when over_limit is true, never on null (unknown)", () => {
    expect(
      deriveAttentionItems({
        ...ALL_CLEAR,
        portfolio_heat: { ...ALL_CLEAR.portfolio_heat, over_limit: true },
      }).find((i) => i.id === "portfolio-heat")
    ).toMatchObject({ severity: "critical", anchor: "portfolio-risk" });

    expect(
      deriveAttentionItems({
        ...ALL_CLEAR,
        portfolio_heat: { ...ALL_CLEAR.portfolio_heat, over_limit: null },
      }).find((i) => i.id === "portfolio-heat")
    ).toBeUndefined();
  });

  it("flags a non-zero risk-gate block count", () => {
    expect(
      deriveAttentionItems({
        ...ALL_CLEAR,
        risk_gate_blocks: { entries: [], count: 1, reason: null },
      }).find((i) => i.id === "risk-gate-blocks")
    ).toMatchObject({ severity: "warning", label: "1 order blocked by the risk gate", anchor: "risk-gate-blocks" });
  });

  it("flags sizing-cap ESCALATION events only, never routine kelly_cap/vol_target/portfolio_gross binds", () => {
    const routine = deriveAttentionItems({
      ...ALL_CLEAR,
      sizing_cap_audit: {
        ...ALL_CLEAR.sizing_cap_audit,
        events: [
          { id: 1, timestamp: null, cycle_id: null, symbol: "NVDA", strategy_id: null, raw_weight: 0.3, final_weight: 0.2, binding_constraint: "kelly_cap", was_capped: true },
          { id: 2, timestamp: null, cycle_id: null, symbol: "SPY", strategy_id: null, raw_weight: 4.0, final_weight: 3.0, binding_constraint: "portfolio_gross", was_capped: true },
        ],
      },
    });
    expect(routine.find((i) => i.id === "sizing-cap-escalation")).toBeUndefined();

    const escalated = deriveAttentionItems({
      ...ALL_CLEAR,
      sizing_cap_audit: {
        ...ALL_CLEAR.sizing_cap_audit,
        events: [
          { id: 3, timestamp: null, cycle_id: null, symbol: "TSLA", strategy_id: null, raw_weight: 0.3, final_weight: 0.1, binding_constraint: "escalation", was_capped: true },
        ],
      },
    });
    expect(escalated.find((i) => i.id === "sizing-cap-escalation")).toMatchObject({
      severity: "warning",
      label: "1 position under sizing-cap escalation",
      anchor: "sizing-cap-audit",
    });
  });

  it("flags a stale heartbeat at the same >120s threshold HeartbeatSection already uses, never on a null (no-heartbeat-file) age", () => {
    expect(
      deriveAttentionItems({
        ...ALL_CLEAR,
        heartbeat: { ...ALL_CLEAR.heartbeat, age_seconds: 121 },
      }).find((i) => i.id === "heartbeat-stale")
    ).toMatchObject({ severity: "critical", anchor: "heartbeat" });

    expect(
      deriveAttentionItems({
        ...ALL_CLEAR,
        heartbeat: { ...ALL_CLEAR.heartbeat, age_seconds: 119 },
      }).find((i) => i.id === "heartbeat-stale")
    ).toBeUndefined();

    // null (no heartbeat file at all) is a DIFFERENT, unavailable state -- not "stale".
    expect(
      deriveAttentionItems({
        ...ALL_CLEAR,
        heartbeat: { ...ALL_CLEAR.heartbeat, age_seconds: null },
      }).find((i) => i.id === "heartbeat-stale")
    ).toBeUndefined();
  });
});

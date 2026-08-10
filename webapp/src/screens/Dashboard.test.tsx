import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dashboard } from "./Dashboard";
import { api } from "../api/client";
import { ApiError, type ObservabilitySummary } from "../api/types";
import {
  mockEtfTransmissionDisabled,
  mockForecastSkillBySymbolEmpty,
  mockHeartbeatNoData,
  mockLatencyHeatmapDisabled,
  mockSizingCapAuditDisabled,
  mockStrategyPnlEmpty,
  mockSystemTelemetryUnavailable,
} from "../api/mock";

// A fully cold-start / all-clear summary -- independent copy of
// Observability.test.tsx's COLD_START (same rationale: that fixture lives in
// a sibling .tsx test file, not a shared module).
const ALL_CLEAR: ObservabilitySummary = {
  portfolio_risk: {
    sharpe_ratio: null, calmar_ratio: null, max_drawdown: null,
    max_drawdown_duration_days: null, cagr: null, n_snapshots: 0,
    min_snapshots_required: 20, reason: "No account snapshots yet.",
  },
  portfolio_heat: {
    heat_pct: null, max_portfolio_heat: 0.06, over_limit: null,
    n_positions: 0, as_of: null, reason: "No account snapshot yet.",
  },
  equity_curve: { range: "1Y", points: [], reason: "No account snapshots yet." },
  regime: {
    as_of: null, market_regime: null, vix: null, sahm_rule: null,
    high_yield_oas: null, yield_curve: null, hmm_risk_on_probability: null,
    kill_switch_active: null, macro_regime_gate_enabled: null,
    macro_kill_switch: null, reason: "No state snapshot yet.",
    macro_gate_writable: false, macro_gate_writable_note: "Writes are disabled.",
  },
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

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>
  );
}

describe("Dashboard screen (R1)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    Object.defineProperty(window, "innerWidth", {
      writable: true,
      configurable: true,
      value: 1024,
    });
  });

  // T1.1: Mount and render checking
  it("renders dashboard title and standard widgets", async () => {
    renderDashboard();
    expect(await screen.findByTestId("dashboard-title")).toBeInTheDocument();
    expect(screen.getByTestId("widget-portfolio-summary")).toBeInTheDocument();
    expect(screen.getByTestId("widget-performance-curve")).toBeInTheDocument();
    expect(screen.getByTestId("widget-activity-feed")).toBeInTheDocument();
    expect(screen.getByTestId("widget-top-pilots")).toBeInTheDocument();
    expect(screen.getByTestId("widget-notebook-export")).toBeInTheDocument();
  });


  // T2.1: Corrupted LocalStorage Handling
  it("does not crash when layout is non-JSON or corrupted", async () => {
    localStorage.setItem("grid-layout-dashboard", "{ invalid json }");
    renderDashboard();
    expect(await screen.findByTestId("dashboard-title")).toBeInTheDocument();
    expect(screen.getByTestId("widget-portfolio-summary")).toBeInTheDocument();
    expect(screen.getByTestId("widget-performance-curve")).toBeInTheDocument();
  });


  // T2.4: Cold-Start 404 handler
  it("renders widget-specific cold-start error when portfolio API fails with 404", async () => {
    vi.spyOn(api, "getPortfolio").mockRejectedValueOnce(
      new ApiError("no account snapshot cached yet", 404)
    );
    renderDashboard();
    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
    expect(screen.getByText("Run the Stockpy pipeline to produce data, then pull to refresh.")).toBeInTheDocument();
  });

});

describe("Dashboard screen — Mission Control attention banner", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the attention banner when the shared derivation finds something notable (the default mock's real circuit-breaker trips + risk-gate blocks)", async () => {
    renderDashboard();
    const banner = await screen.findByTestId("dashboard-attention-banner");
    // 3 items in the default mock: 1 critical + 1 warning circuit-breaker
    // bucket, plus 1 risk-gate-blocks bucket -- see observabilityAttention
    // .test.ts for the derivation itself; this only confirms Dashboard wires
    // it up and surfaces the count.
    expect(banner.textContent).toMatch(/3 items? needs? attention/);
  });

  it("renders no banner at all when the summary is fully all-clear — never a fabricated alert", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(ALL_CLEAR);
    renderDashboard();
    expect(await screen.findByTestId("dashboard-title")).toBeInTheDocument();
    expect(screen.queryByTestId("dashboard-attention-banner")).not.toBeInTheDocument();
  });
});

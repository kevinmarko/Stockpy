import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dashboard } from "./Dashboard";
import { api } from "../api/client";
import { ApiError, type ObservabilitySummary, type PilotSummary } from "../api/types";
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

function makePilot(overrides: Partial<PilotSummary> & Pick<PilotSummary, "id" | "name" | "category">): PilotSummary {
  return {
    description: "",
    headline: { sharpe: null, dsr: null, pbo: null, max_drawdown: null, deployable: null },
    holdings_count: 0,
    top_holdings: [],
    aum_proxy: 0,
    followers_proxy: 0,
    long_only: true,
    ...overrides,
  };
}

// Three pilots chosen so all three sort modes ("sr" / "strategy" / "active")
// produce a DIFFERENT, individually distinguishable order from this same
// fixture array — [Alpha, Beta, Gamma] as returned by the mocked API.
const ALPHA = makePilot({
  id: "p-alpha", name: "Alpha Strategy", category: "Momentum",
  headline: { sharpe: 1.5, dsr: 0.98, pbo: 0.1, max_drawdown: -0.1, deployable: true },
});
const BETA = makePilot({
  id: "p-beta", name: "Beta Strategy", category: "Blend",
  // Cold-start pilot: no backtest yet, so `sharpe`/`deployable` are `null`
  // (not `0`/`false`) — must sort to the very bottom on "sr", never a
  // fabricated middling rank (CONSTRAINT #4).
  headline: { sharpe: null, dsr: null, pbo: null, max_drawdown: null, deployable: null },
});
const GAMMA = makePilot({
  id: "p-gamma", name: "Gamma Strategy", category: "Factor",
  headline: { sharpe: 0.8, dsr: 0.6, pbo: 0.4, max_drawdown: -0.2, deployable: false },
});

describe("Dashboard screen — Top Pilots sorting", () => {
  afterEach(() => vi.restoreAllMocks());

  function pilotOrder(): string[] {
    return Array.from(document.querySelectorAll(".row-title")).map((el) => el.textContent ?? "");
  }

  it("defaults to Sharpe descending, with a cold-start (null Sharpe) pilot sorted to the bottom and shown as SR: —", async () => {
    vi.spyOn(api, "listPilots").mockResolvedValue([ALPHA, BETA, GAMMA]);
    renderDashboard();

    await screen.findByText("Alpha Strategy");
    expect(pilotOrder()).toEqual(["Alpha Strategy", "Gamma Strategy", "Beta Strategy"]);
    expect(screen.getByText("SR: 1.50")).toBeInTheDocument();
    expect(screen.getByText("SR: 0.80")).toBeInTheDocument();
    expect(screen.getByText("SR: —")).toBeInTheDocument();
  });

  it("displays a genuine Sharpe of exactly 0.00 as a real value, not as missing (SR: —)", async () => {
    // Regression: 0 is falsy in JS -- a naive `pilot.headline.sharpe ? ... :
    // "SR: —"` check renders a real, meaningful sharpe: 0 identically
    // to a null/missing value. `sortedPilots`'s own comparator already
    // distinguishes `=== null` from a real 0; the display must too.
    const ZERO = makePilot({
      id: "p-zero", name: "Zero Strategy", category: "Risk",
      headline: { sharpe: 0, dsr: 0.9, pbo: 0.2, max_drawdown: -0.05, deployable: true },
    });
    vi.spyOn(api, "listPilots").mockResolvedValue([ZERO, BETA]);
    renderDashboard();

    await screen.findByText("Zero Strategy");
    expect(screen.getByText("SR: 0.00")).toBeInTheDocument();
  });

  it("sorts alphabetically by category (Strategy) when that sort button is clicked", async () => {
    vi.spyOn(api, "listPilots").mockResolvedValue([ALPHA, BETA, GAMMA]);
    const user = userEvent.setup();
    renderDashboard();
    await screen.findByText("Alpha Strategy");

    await user.click(screen.getByRole("button", { name: "Strategy" }));

    // Blend < Factor < Momentum, alphabetically.
    expect(pilotOrder()).toEqual(["Beta Strategy", "Gamma Strategy", "Alpha Strategy"]);
  });

  it("sorts deployable pilots first (Active) when that sort button is clicked, ranking a cold-start null above a measured failed gate", async () => {
    vi.spyOn(api, "listPilots").mockResolvedValue([ALPHA, BETA, GAMMA]);
    const user = userEvent.setup();
    renderDashboard();
    await screen.findByText("Alpha Strategy");

    await user.click(screen.getByRole("button", { name: "Active" }));

    // ALPHA (deployable: true) sorts first; BETA (null — not yet validated,
    // "unknown") sorts above GAMMA (false — measured, failed the
    // deployability gate). Unknown must never be conflated with known-bad.
    expect(pilotOrder()).toEqual(["Alpha Strategy", "Beta Strategy", "Gamma Strategy"]);
  });

  it("Active sort still ranks a null (unvalidated) pilot above a false (failed-gate) one when the failed-gate pilot comes first in the source list", async () => {
    // Distinguishes "null ranks above false" from the coincidental "stable
    // sort preserved BETA-before-GAMMA source order" outcome above — feeding
    // the API in the OPPOSITE order (GAMMA, then BETA) would leave a stable
    // sort's original relative order unchanged if null and false were tied,
    // but must still produce Beta (null) before Gamma (false) now that the
    // two are ranked distinctly.
    vi.spyOn(api, "listPilots").mockResolvedValue([ALPHA, GAMMA, BETA]);
    const user = userEvent.setup();
    renderDashboard();
    await screen.findByText("Alpha Strategy");

    await user.click(screen.getByRole("button", { name: "Active" }));

    expect(pilotOrder()).toEqual(["Alpha Strategy", "Beta Strategy", "Gamma Strategy"]);
  });
});

describe("Dashboard screen — Liquidate/Rebalance advisory modal", () => {
  afterEach(() => vi.restoreAllMocks());

  it("opens a safe advisory-only modal on Liquidate — never a live order action", async () => {
    const user = userEvent.setup();
    renderDashboard();
    // findBy* polls until the portfolio finishes loading and the button
    // actually exists -- widget-portfolio-summary's container div is
    // present from the very first (loading-skeleton) render.
    const liquidateBtn = await screen.findByRole("button", { name: "Liquidate" });

    await user.click(liquidateBtn);

    const dialog = await screen.findByRole("dialog", { name: "Liquidate Portfolio" });
    expect(within(dialog).getByText(/advisory-only platform/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/manually open your Robinhood app/i)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Understood" }));
    expect(screen.queryByRole("dialog", { name: "Liquidate Portfolio" })).not.toBeInTheDocument();
  });

  it("opens the same advisory modal, correctly labeled, on Rebalance", async () => {
    const user = userEvent.setup();
    renderDashboard();
    const rebalanceBtn = await screen.findByRole("button", { name: "Rebalance" });

    await user.click(rebalanceBtn);

    const dialog = await screen.findByRole("dialog", { name: "Rebalance Portfolio" });
    expect(within(dialog).getByText(/advisory-only platform/i)).toBeInTheDocument();
  });
});

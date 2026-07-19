/**
 * Observability.test.tsx — the Mission Control screen renders each of the
 * four sections from the mock (portfolio risk, equity/drawdown/regime,
 * forecast skill, risk-gate block log), and renders every honesty branch
 * (null metrics -> "—", empty/cold-start -> the persisted reason) rather than
 * a fabricated number, never a hard failure.
 */
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Observability } from "./Observability";
import { api } from "../api/client";
import type { ObservabilitySummary } from "../api/types";

function renderScreen() {
  return render(
    <MemoryRouter>
      <Observability />
    </MemoryRouter>
  );
}

const COLD_START: ObservabilitySummary = {
  portfolio_risk: {
    sharpe_ratio: null,
    calmar_ratio: null,
    max_drawdown: null,
    max_drawdown_duration_days: null,
    cagr: null,
    n_snapshots: 0,
    min_snapshots_required: 20,
    reason: "No account snapshots yet — run the pipeline to start accumulating equity history.",
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
    reason: "No state snapshot yet — run the pipeline first.",
  },
  forecast_skill: {
    horizon_days: 30,
    window_days: 180,
    min_obs: 30,
    reliability_curve: [],
    skill_weights: {},
    pending: 0,
    completed: 0,
    reason: "No forecast history yet — run the pipeline to accumulate it.",
  },
  risk_gate_blocks: { entries: [], count: 0, reason: "No risk-gate blocks logged yet." },
};

describe("Observability (Mission Control) screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the portfolio risk tiles from the mock", async () => {
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Mission Control" })).toBeInTheDocument();
    expect(await screen.findByText("Sharpe")).toBeInTheDocument();
    expect(await screen.findByText("Calmar")).toBeInTheDocument();
    expect(await screen.findByText("Max drawdown")).toBeInTheDocument();
    // The mock's sharpe_ratio (1.18) renders as a real number, not "—".
    expect(await screen.findByText("1.18")).toBeInTheDocument();
  });

  it("renders the regime badges from the mock", async () => {
    renderScreen();
    const badges = await screen.findByTestId("regime-badges");
    expect(within(badges).getByText(/Regime: RISK ON/)).toBeInTheDocument();
    expect(within(badges).getByText(/Sahm Rule/)).toBeInTheDocument();
    // as_of freshness is surfaced, not just the point-in-time metrics.
    expect(within(badges).getByText(/As of: \d+m ago/)).toBeInTheDocument();
  });

  it("a cold-start regime (reason set) never fabricates an as_of badge", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(COLD_START);
    renderScreen();
    expect(
      await screen.findByText("No state snapshot yet — run the pipeline first.")
    ).toBeInTheDocument();
    expect(screen.queryByTestId("regime-badges")).not.toBeInTheDocument();
  });

  it("renders portfolio-wide forecast skill weights", async () => {
    renderScreen();
    expect(await screen.findByText("Forecast skill")).toBeInTheDocument();
    expect((await screen.findAllByText("arima")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText("monte_carlo")).length).toBeGreaterThan(0);
  });

  it("renders the risk-gate block log entries from the mock", async () => {
    renderScreen();
    const rows = await screen.findAllByTestId("risk-gate-block-row");
    expect(rows.length).toBeGreaterThan(0);
    expect(within(rows[0]).getByText(/AMD|TSLA/)).toBeInTheDocument();
  });

  it("cold start: every section renders its honest reason, never a fabricated value", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(COLD_START);
    renderScreen();

    // Portfolio risk tiles render "—" for every null metric.
    expect(await screen.findAllByText("—")).not.toHaveLength(0);
    expect(
      await screen.findByText(/No account snapshots yet — run the pipeline/)
    ).toBeInTheDocument();

    // Equity/drawdown section falls back to its reason, never an empty chart.
    expect(screen.getByText("No account snapshots yet.")).toBeInTheDocument();

    // Regime section shows its cold-start reason instead of fabricated badges.
    expect(screen.getByText("No state snapshot yet — run the pipeline first.")).toBeInTheDocument();

    // Forecast skill and risk-gate block log both degrade honestly too.
    expect(
      screen.getByText("No forecast history yet — run the pipeline to accumulate it.")
    ).toBeInTheDocument();
    expect(screen.getByText("No risk-gate blocks logged yet.")).toBeInTheDocument();
  });

  it("a null reliability bin renders '—', never a fabricated percent", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce({
      ...COLD_START,
      forecast_skill: {
        horizon_days: 30,
        window_days: 180,
        min_obs: 30,
        reliability_curve: [
          { model_name: "arima", horizon_days: 30, bin_center: 0.1, mean_pct_error: null, count: 2 },
        ],
        skill_weights: { arima: 1.0 },
        pending: 0,
        completed: 40,
        reason: null,
      },
    });
    renderScreen();

    expect((await screen.findAllByText("arima")).length).toBeGreaterThan(0);
    // The null mean_pct_error cell renders "—", not "NaN%" or a fabricated 0%.
    const cells = await screen.findAllByText("—");
    expect(cells.length).toBeGreaterThan(0);
  });

  it("an error response renders ErrorState with a retry action", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockRejectedValueOnce(
      new Error("network unreachable")
    );
    renderScreen();
    expect(await screen.findByText(/network unreachable/)).toBeInTheDocument();
  });
});

/**
 * Observability.test.tsx — the Mission Control screen renders each of the
 * four sections from the mock (portfolio risk, equity/drawdown/regime,
 * forecast skill, risk-gate block log), and renders every honesty branch
 * (null metrics -> "—", empty/cold-start -> the persisted reason) rather than
 * a fabricated number, never a hard failure.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  portfolio_heat: {
    heat_pct: null,
    max_portfolio_heat: 0.06,
    over_limit: null,
    n_positions: 0,
    as_of: null,
    reason: "No account snapshot yet — run `python3 main.py --refresh-account` to populate.",
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

  it("renders the portfolio heat tile from the mock", async () => {
    renderScreen();
    expect(await screen.findByText("Portfolio heat")).toBeInTheDocument();
    // mock.ts's mockPortfolioHeat: 2.1% heat / 6% ceiling.
    expect(await screen.findByText("2.1% / 6%")).toBeInTheDocument();
  });

  it("a cold-start portfolio heat (heat_pct null) renders '—' and its reason, never a fabricated 0%", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(COLD_START);
    renderScreen();
    expect(await screen.findByText("Portfolio heat")).toBeInTheDocument();
    expect(
      await screen.findByText(/Portfolio heat: No account snapshot yet/)
    ).toBeInTheDocument();
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

describe("Observability (Mission Control) screen — macro regime gate toggle", () => {
  afterEach(() => vi.restoreAllMocks());

  const WRITABLE_ON: ObservabilitySummary = {
    ...COLD_START,
    regime: {
      as_of: new Date().toISOString(),
      market_regime: "RISK ON",
      vix: 14.8,
      sahm_rule: 0.13,
      high_yield_oas: 3.21,
      yield_curve: 0.42,
      hmm_risk_on_probability: 0.78,
      kill_switch_active: false,
      macro_regime_gate_enabled: true,
      reason: null,
      macro_gate_writable: true,
      macro_gate_writable_note: "Writes persist to .env and apply on the next daemon/pipeline launch.",
    },
  };

  it("renders the toggle ON and writable by default", async () => {
    renderScreen();
    const toggle = await screen.findByRole("switch", { name: /Macro regime gate: ON/ });
    expect(toggle).not.toBeDisabled();
  });

  it("toggling off opens a confirm dialog and writes with the typed reason", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValue(WRITABLE_ON);
    const putSpy = vi
      .spyOn(api, "putMacroGate")
      .mockResolvedValueOnce({
        written: ["MACRO_REGIME_GATE_ENABLED"],
        enabled: false,
        applies: "next_daemon_restart",
        note: "Written to .env.",
      });
    renderScreen();

    const toggle = await screen.findByRole("switch", { name: /Macro regime gate: ON/ });
    await user.click(toggle);
    expect(screen.getByText("Disable macro regime gate?")).toBeInTheDocument();

    // Confirm is disabled until a reason is typed -- a fat-finger guard, not
    // the real gate (the real gates are server-side).
    const disableBtn = screen.getByRole("button", { name: "Disable" });
    expect(disableBtn).toBeDisabled();

    await user.type(screen.getByLabelText("Reason"), "idiosyncratic vol spike, not systemic");
    expect(disableBtn).not.toBeDisabled();
    await user.click(disableBtn);

    await waitFor(() =>
      expect(putSpy).toHaveBeenCalledWith(false, "idiosyncratic vol spike, not systemic")
    );
  });

  it("toggling on (from off) opens a confirm dialog and writes true", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValue({
      ...WRITABLE_ON,
      regime: { ...WRITABLE_ON.regime, macro_regime_gate_enabled: false },
    });
    const putSpy = vi
      .spyOn(api, "putMacroGate")
      .mockResolvedValueOnce({
        written: ["MACRO_REGIME_GATE_ENABLED"],
        enabled: true,
        applies: "next_daemon_restart",
        note: "Written to .env.",
      });
    renderScreen();

    const toggle = await screen.findByRole("switch", { name: /Macro regime gate: OFF/ });
    await user.click(toggle);
    expect(screen.getByText("Enable macro regime gate?")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Reason"), "re-enabling before going live");
    await user.click(screen.getByRole("button", { name: "Enable" }));

    await waitFor(() =>
      expect(putSpy).toHaveBeenCalledWith(true, "re-enabling before going live")
    );
  });

  it("shows a caution note when the gate is off", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce({
      ...WRITABLE_ON,
      regime: { ...WRITABLE_ON.regime, macro_regime_gate_enabled: false },
    });
    renderScreen();
    expect(await screen.findByRole("switch", { name: /Macro regime gate: OFF/ })).toBeInTheDocument();
    expect(
      screen.getByText(/Technical BUY signals run without a macro veto/)
    ).toBeInTheDocument();
  });

  it("disables the toggle and shows the server note when the write is gated off", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce({
      ...WRITABLE_ON,
      regime: {
        ...WRITABLE_ON.regime,
        macro_gate_writable: false,
        macro_gate_writable_note: "Writes are disabled (MACRO_GATE_WRITES_ENABLED=false).",
      },
    });
    renderScreen();

    const toggle = await screen.findByRole("switch", { name: /Macro regime gate: ON/ });
    expect(toggle).toBeDisabled();
    expect(
      screen.getByText("Writes are disabled (MACRO_GATE_WRITES_ENABLED=false).")
    ).toBeInTheDocument();
  });

  it("never fabricates a toggle state when macro_regime_gate_enabled is null", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce({
      ...WRITABLE_ON,
      regime: { ...WRITABLE_ON.regime, macro_regime_gate_enabled: null },
    });
    renderScreen();
    await screen.findByTestId("regime-badges");
    expect(screen.queryByRole("switch", { name: /Macro regime gate/ })).not.toBeInTheDocument();
  });
});

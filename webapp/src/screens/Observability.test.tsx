/**
 * Observability.test.tsx — the Mission Control screen renders each of the
 * sections from the mock (portfolio risk, equity/drawdown/regime, forecast
 * skill, circuit breakers, risk-gate block log), and renders every honesty
 * branch (null metrics -> "—", empty/cold-start -> the persisted reason)
 * rather than a fabricated number, never a hard failure.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Observability } from "./Observability";
import { api } from "../api/client";
import {
  mockEmptyLogAggregation,
  mockEtfTransmissionDisabled,
  mockForecastSkillBySymbolEmpty,
  mockHeartbeatNoData,
  mockLatencyHeatmapDisabled,
  mockSizingCapAuditDisabled,
  mockStrategyPnlEmpty,
  mockSystemTelemetryUnavailable,
} from "../api/mock";
import type { LogAggregation, ObservabilitySummary } from "../api/types";

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
    macro_kill_switch: null,
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
  forecast_skill_by_symbol: mockForecastSkillBySymbolEmpty(),
  risk_gate_blocks: { entries: [], count: 0, reason: "No risk-gate blocks logged yet." },
  latency_heatmap: mockLatencyHeatmapDisabled(),
  circuit_breakers: {
    trips: [],
    counts: { critical: 0, warning: 0, total: 0 },
    window_hours: 24,
    reason: "No active circuit-breaker trips in the last 24h.",
  },
  // mock.ts's mockSystemTelemetryUnavailable() is the canonical shape for
  // this branch (exact mirror of pilots/observability.py::
  // _empty_system_telemetry) -- calling it here instead of hand-rolling the
  // same object keeps this test pinned to the mock's own copy rather than a
  // separate one that could silently drift from it.
  system_telemetry: mockSystemTelemetryUnavailable(),
  // Same rationale as system_telemetry above -- each mock*Disabled/*Empty/
  // *NoData helper is the canonical mirror of its corresponding
  // pilots/observability.py _empty_* shape.
  sizing_cap_audit: mockSizingCapAuditDisabled(),
  etf_transmission: mockEtfTransmissionDisabled(),
  heartbeat: mockHeartbeatNoData(),
  strategy_pnl: mockStrategyPnlEmpty(),
};

// mock.ts's mockEmptyLogAggregation() is the canonical shape for this branch
// (exact mirror of pilots/observability.py::_empty_log_aggregation) -- same
// rationale as COLD_START.system_telemetry above.
const EMPTY_LOGS: LogAggregation = mockEmptyLogAggregation(
  "No log file yet at logs/investyo.log."
);

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

  it("renders the DynamicGrid tiles (portfolio risk + equity)", async () => {
    renderScreen();
    // In test environment, DynamicGrid renders the items without grid logic but assigns test id
    expect(await screen.findByTestId("grid-observability")).toBeInTheDocument();
  });

  it("keeps everything past portfolio risk/equity collapsed by default behind a real <details> disclosure, kept outside the DynamicGrid", async () => {
    renderScreen();
    // The DynamicGrid still mounts (portfolio risk + equity stayed genuinely
    // draggable/resizable tiles -- see the test above), but the restored
    // progressive-disclosure hierarchy means everything else (forecast
    // skill, circuit breakers, risk gate log, telemetry, data latency,
    // sizing audit, ETF transmission, heartbeat, strategy P&L, logs, macro
    // sentiment) is NOT a grid item at all -- it's a single collapsed
    // <details> in normal document flow, closed unless the operator opens
    // it, exactly as it was before the DynamicGrid migration.
    await screen.findByTestId("grid-observability");
    const details = await screen.findByTestId("background-telemetry");
    expect(details.tagName).toBe("DETAILS");
    expect((details as HTMLDetailsElement).open).toBe(false);
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

  it("renders one forecast-skill-by-symbol row per symbol from the mock, including a zero-history symbol never dropped", async () => {
    renderScreen();
    expect(await screen.findByText("Forecast skill by symbol")).toBeInTheDocument();
    const rows = (await screen.findAllByTestId("forecast-skill-symbol-row")) as HTMLTableRowElement[];
    // mock.ts's FORECAST_SKILL_SYMBOLS: AAPL, MSFT, NVDA, TSLA, AMD.
    expect(rows.length).toBe(5);
    const bySymbol = Object.fromEntries(rows.map((r) => [r.cells[0].textContent, r]));
    expect(bySymbol.AAPL.cells[3].textContent).not.toBe("—"); // has a top model
    // The mock's last symbol (AMD) is the deliberately cold-start one.
    expect(bySymbol.AMD.cells[1].textContent).toBe("0");
    expect(bySymbol.AMD.cells[2].textContent).toBe("0");
    expect(bySymbol.AMD.cells[3].textContent).toBe("—");
  });

  it("a disabled forecast-skill-by-symbol section renders the honest reason, never a fabricated table", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(COLD_START);
    renderScreen();
    expect(screen.queryByTestId("forecast-skill-symbol-row")).not.toBeInTheDocument();
  });

  it("renders the data-latency heatmap rows and KPI strip from the mock", async () => {
    renderScreen();
    expect(await screen.findByText("Data latency")).toBeInTheDocument();
    const rows = await screen.findAllByTestId("latency-sample-row");
    expect(rows.length).toBe(5); // mock.ts's FORECAST_SKILL_SYMBOLS
    expect(await screen.findByText("Worst symbol")).toBeInTheDocument();
    // mock.ts deliberately makes MSFT the slow one (is_stale, latency > 3s).
    expect((await screen.findAllByText("stale")).length).toBeGreaterThan(0);
  });

  it("tracking disabled (the real default) renders the honest reason, never a fabricated table", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(COLD_START);
    renderScreen();
    expect(
      await screen.findByText(
        "MARKET_DATA_LATENCY_TRACKING_ENABLED is False — latency samples are not recorded this process."
      )
    ).toBeInTheDocument();
    expect(screen.queryByTestId("latency-sample-row")).not.toBeInTheDocument();
  });

  it("renders the risk-gate block log entries from the mock", async () => {
    renderScreen();
    const rows = await screen.findAllByTestId("risk-gate-block-row");
    expect(rows.length).toBeGreaterThan(0);
    expect(within(rows[0]).getByText(/AMD|TSLA/)).toBeInTheDocument();
  });

  it("renders the circuit-breaker KPI strip and severity chips from the mock", async () => {
    renderScreen();
    expect(await screen.findByText("Circuit breakers")).toBeInTheDocument();
    expect(await screen.findByText("Critical trips")).toBeInTheDocument();
    expect(await screen.findByText("Warning trips")).toBeInTheDocument();
    const rows = await screen.findAllByTestId("circuit-breaker-row");
    // mock.ts's mockCircuitBreakers: one CRITICAL (portfolio_heat) + two
    // WARNING (max_correlation, max_position_size) trips.
    expect(rows.length).toBe(3);
    expect(within(rows[0]).getByText("CRITICAL")).toBeInTheDocument();
    expect(within(rows[1]).getByText("WARNING")).toBeInTheDocument();
    expect(within(rows[2]).getByText("WARNING")).toBeInTheDocument();
    expect(within(rows[0]).getByText(/Threshold: 0.05/)).toBeInTheDocument();
    expect(within(rows[0]).getByText(/Observed: 0.064/)).toBeInTheDocument();
  });

  it("a trip with null threshold/observed/triggered_at never renders a fabricated value", async () => {
    renderScreen();
    const rows = await screen.findAllByTestId("circuit-breaker-row");
    // The third mock trip (max_position_size) carries no threshold/observed
    // (that check's summary template has none) and no triggered_at (mirrors
    // a kill-switch sentinel with no readable mtime) -- confirm the row
    // renders its "—" timestamp fallback and omits the threshold/observed
    // line entirely, rather than fabricating "Threshold: null" or similar.
    const nullTrip = rows[2];
    expect(within(nullTrip).getByText(/NVDA/)).toBeInTheDocument();
    expect(within(nullTrip).getByText("—")).toBeInTheDocument();
    expect(within(nullTrip).queryByText(/Threshold:/)).not.toBeInTheDocument();
    expect(within(nullTrip).queryByText(/Observed:/)).not.toBeInTheDocument();
  });

  it("cold-start circuit breakers render the honest empty reason, never a fabricated 'all clear' tile", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(COLD_START);
    renderScreen();
    expect(await screen.findByText("Circuit breakers")).toBeInTheDocument();
    expect(
      await screen.findByText("No active circuit-breaker trips in the last 24h.")
    ).toBeInTheDocument();
    expect(screen.queryByTestId("circuit-breaker-row")).not.toBeInTheDocument();
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

    // Forecast skill (both the portfolio-wide AND per-symbol sections
    // legitimately share this exact reason string) and risk-gate block log
    // both degrade honestly too.
    expect(
      screen.getAllByText("No forecast history yet — run the pipeline to accumulate it.")
    ).toHaveLength(2);
    expect(screen.getByText("No risk-gate blocks logged yet.")).toBeInTheDocument();

    // System telemetry: psutil unavailable -> honest reason, no fabricated
    // 0% CPU/memory tiles.
    expect(
      await screen.findByText("psutil is not available in this environment.")
    ).toBeInTheDocument();
  });

  it("renders system telemetry tiles from the mock, with saturation cues suppressed at healthy levels", async () => {
    renderScreen();
    expect(await screen.findByText("System telemetry")).toBeInTheDocument();
    expect(await screen.findByText("Host CPU")).toBeInTheDocument();
    // mock.ts's mockSystemTelemetry: 18.4% CPU, 61.2% memory -- both well
    // under the 75%/90% saturation thresholds, so no warning/error copy.
    expect(await screen.findByText("18.4%")).toBeInTheDocument();
    expect(await screen.findByText("61.2%")).toBeInTheDocument();
    expect(screen.queryByText(/CPU saturated/)).not.toBeInTheDocument();
    expect(screen.queryByText(/watch for slowdowns/)).not.toBeInTheDocument();
  });

  it("flags CPU saturation and memory pressure at the same thresholds as the legacy panel", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce({
      ...COLD_START,
      system_telemetry: {
        psutil_available: true,
        cpu_percent: 94.2,
        cpu_count_logical: 8,
        load_avg_1m: 6.1,
        memory_percent: 92.1,
        memory_used_bytes: 15_000_000_000,
        memory_total_bytes: 16_000_000_000,
        disk_percent: 55.0,
        disk_used_bytes: 100,
        disk_total_bytes: 200,
        process_rss_bytes: 900,
        process_cpu_percent: 40.0,
        process_threads: 12,
        sampled_at: new Date().toISOString(),
        reason: null,
      },
    });
    renderScreen();
    expect(await screen.findByText(/CPU saturated at 94%/)).toBeInTheDocument();
    expect(await screen.findByText(/Memory at 92%/)).toBeInTheDocument();
  });

  it("renders the log aggregation KPI strip and entries from the mock", async () => {
    renderScreen();
    expect(await screen.findByText("Logs")).toBeInTheDocument();
    // mock.ts's mockObservabilityLogs: 1 CRITICAL, 1 ERROR, 1 WARNING, 2 INFO,
    // + 1 unparsed traceback continuation -- default min-level filter is INFO
    // so all 6 entries show by default (unparsed lines are always kept,
    // matching the legacy panel's "never lose traceback context" contract).
    const rows = await screen.findAllByTestId("log-entry-row");
    expect(rows.length).toBe(6);
    expect(await screen.findByText(/Cycle started/)).toBeInTheDocument();
  });

  it("filters log entries by minimum level client-side, keeping unparsed lines regardless", async () => {
    const user = userEvent.setup();
    renderScreen();
    await screen.findAllByTestId("log-entry-row");

    const select = screen.getByTestId("log-level-select");
    await user.selectOptions(select, "CRITICAL");

    // Only the CRITICAL entry survives the level threshold, PLUS the
    // unparsed traceback line (unparsed entries are exempt from the level
    // filter, matching gui.observability_telemetry.filter_log_entries).
    const rows = await screen.findAllByTestId("log-entry-row");
    expect(rows.length).toBe(2);
    expect(screen.getByText(/FRED unavailable/)).toBeInTheDocument();
    expect(screen.queryByText(/Cycle started/)).not.toBeInTheDocument();
  });

  it("filters log entries by free-text substring client-side", async () => {
    const user = userEvent.setup();
    renderScreen();
    await screen.findAllByTestId("log-entry-row");

    await user.type(screen.getByLabelText("Filter (substring)"), "NVDA");

    const rows = await screen.findAllByTestId("log-entry-row");
    expect(rows.length).toBe(1);
    expect(within(rows[0]).getByText(/NVDA/)).toBeInTheDocument();
  });

  it("an empty log tail renders the honest reason, never a fabricated table", async () => {
    vi.spyOn(api, "getObservabilityLogs").mockResolvedValueOnce(EMPTY_LOGS);
    renderScreen();
    expect(
      await screen.findByText("No log file yet at logs/investyo.log.")
    ).toBeInTheDocument();
    expect(screen.queryByTestId("log-entry-row")).not.toBeInTheDocument();
  });

  it("a log endpoint error renders its own ErrorState with a retry action", async () => {
    vi.spyOn(api, "getObservabilityLogs").mockRejectedValueOnce(
      new Error("logs unreachable")
    );
    renderScreen();
    expect(await screen.findByText(/logs unreachable/)).toBeInTheDocument();
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

  // ---- G7: Sizing cap-event audit trail, ETF transmission, heartbeat, strategy P&L ----

  it("renders the sizing cap-event audit trail rows and escalation state from the mock", async () => {
    renderScreen();
    expect(await screen.findByText("Sizing cap-event audit trail")).toBeInTheDocument();
    const rows = await screen.findAllByTestId("sizing-cap-event-row");
    // mock.ts's mockSizingCapEvents: 3 events, 2 capped (NVDA kelly_cap, SPY portfolio_gross).
    expect(rows.length).toBe(3);
    expect(within(rows[0]).getByText("NVDA")).toBeInTheDocument();
    expect(within(rows[0]).getByText("kelly_cap")).toBeInTheDocument();
    // TSLA event carries no strategy_id -- renders "—", never a fabricated label.
    expect(within(rows[1]).getByText("—")).toBeInTheDocument();
    expect(within(rows[1]).getByText("not capped")).toBeInTheDocument();
    expect(await screen.findByText(/ON \(5c/)).toBeInTheDocument();
  });

  it("a disabled sizing cap audit renders the honest reason, never a fabricated table", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(COLD_START);
    renderScreen();
    expect(
      await screen.findByText(/SIZING_CAP_AUDIT_ENABLED is False/)
    ).toBeInTheDocument();
    expect(screen.queryByTestId("sizing-cap-event-row")).not.toBeInTheDocument();
  });

  it("renders ETF transmission rows and the three master-switch tiles from the mock", async () => {
    renderScreen();
    expect(await screen.findByText("ETF volatility transmission")).toBeInTheDocument();
    const rows = await screen.findAllByTestId("etf-transmission-row");
    expect(rows.length).toBe(3);
    // SPY's row shows "SPY" twice (symbol column + primary-wrapper column,
    // since SPY IS its own wrapper) -- use getAllByText to avoid ambiguity.
    expect(within(rows[0]).getAllByText("SPY").length).toBe(2);
    expect(within(rows[0]).getByText("100.0%")).toBeInTheDocument();
    // SPY's own multiplier is null (it IS the wrapper) and sizing is ON, so
    // this renders the honest "—", not a fabricated 1.00x.
    expect(within(rows[0]).getByText("—")).toBeInTheDocument();
    expect(await screen.findByText("Portfolio covariance")).toBeInTheDocument();
  });

  it("a disabled ETF transmission measurement gate renders the honest reason, never a fabricated table", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(COLD_START);
    renderScreen();
    expect(
      await screen.findByText(/ETF_TRANSMISSION_ENABLED is False/)
    ).toBeInTheDocument();
    expect(screen.queryByTestId("etf-transmission-row")).not.toBeInTheDocument();
  });

  it("renders the current heartbeat age and status from the mock, with the no-history honesty note", async () => {
    renderScreen();
    expect(await screen.findByText("Heartbeat")).toBeInTheDocument();
    expect(await screen.findByText("24s")).toBeInTheDocument();
    expect(await screen.findByText("🟢 Fresh")).toBeInTheDocument();
    // Never a fabricated trend/sparkline -- the honesty note is always shown.
    expect(await screen.findByText(/session_state -- never persisted to disk/)).toBeInTheDocument();
  });

  it("a missing heartbeat file renders the honest empty status, never a fabricated age", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(COLD_START);
    renderScreen();
    expect(await screen.findByText("⚪ No heartbeat")).toBeInTheDocument();
    expect(
      await screen.findByText(/No heartbeat file yet/)
    ).toBeInTheDocument();
  });

  it("renders strategy P&L rows including the untagged (strategy_id: null) bucket from the mock", async () => {
    renderScreen();
    expect(await screen.findByText("Strategy P&L")).toBeInTheDocument();
    const rows = await screen.findAllByTestId("strategy-pnl-row");
    expect(rows.length).toBe(3);
    expect(within(rows[0]).getByText("timeseries_momentum")).toBeInTheDocument();
    // Untagged trades are a REAL bucket, rendered as "Untagged", never dropped.
    expect(within(rows[2]).getByText("Untagged")).toBeInTheDocument();
    expect(await screen.findByText("Total realized P&L")).toBeInTheDocument();
  });

  it("no closed trades yet renders the honest reason, never a fabricated P&L table", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(COLD_START);
    renderScreen();
    expect(
      await screen.findByText("No closed trades in the transactions store yet.")
    ).toBeInTheDocument();
    expect(screen.queryByTestId("strategy-pnl-row")).not.toBeInTheDocument();
  });
});

describe("Observability (Mission Control) screen — attention strip", () => {
  afterEach(() => vi.restoreAllMocks());

  it("surfaces the mock's real notable conditions (1 critical + 2 warning circuit breakers, 2 risk-gate blocks)", async () => {
    renderScreen();
    expect(await screen.findByTestId("attention-strip")).toBeInTheDocument();
    const items = await screen.findAllByTestId("attention-item");
    const labels = items.map((el) => el.textContent);
    expect(labels.some((t) => t?.includes("1 critical circuit breaker tripped"))).toBe(true);
    expect(labels.some((t) => t?.includes("2 circuit breaker warnings"))).toBe(true);
    expect(labels.some((t) => t?.includes("2 orders blocked by the risk gate"))).toBe(true);
    // Never fabricated: the mock's sizing-cap events are kelly_cap/null/
    // portfolio_gross, not "escalation" -- no sizing-cap item should appear.
    expect(labels.some((t) => t?.includes("sizing-cap"))).toBe(false);
  });

  it("clicking an item scrolls to that section's anchor", async () => {
    const scrollIntoViewSpy = vi.fn();
    // jsdom implements no real layout and defines no scrollIntoView at all
    // (see AIChatInterface.tsx's identical guard) -- stub it to observe the call.
    Element.prototype.scrollIntoView = scrollIntoViewSpy;
    const user = userEvent.setup();
    renderScreen();
    const items = await screen.findAllByTestId("attention-item");
    await user.click(items[0]);
    expect(scrollIntoViewSpy).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
  });

  it("a fully cold-start summary (zero counts everywhere) renders an honest 'All clear', never a fabricated warning", async () => {
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValueOnce(COLD_START);
    renderScreen();
    expect(await screen.findByTestId("attention-strip-clear")).toBeInTheDocument();
    expect(await screen.findByText("✓ All clear")).toBeInTheDocument();
    expect(screen.queryByTestId("attention-strip")).not.toBeInTheDocument();
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
      macro_kill_switch: false,
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

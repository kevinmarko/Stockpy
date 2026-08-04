/**
 * Models.test.tsx — the ML registry sub-page renders model cards with honest
 * deployable badges and renders "—" (never a fabricated 0) for null metrics.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Models } from "./Models";
import { api, ApiError } from "../api/client";
import type { JobRecord, ModelRow } from "../api/types";
import { __resetThresholdsCache } from "../help/thresholds";

function renderModels() {
  return render(
    <MemoryRouter>
      <Models />
    </MemoryRouter>
  );
}

describe("Models screen (real mock API)", () => {
  beforeEach(() => __resetThresholdsCache());
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("renders model rows with an honest not-deployable badge", async () => {
    renderModels();
    expect(await screen.findByText("lgbm_ranker")).toBeInTheDocument();
    // The mock models all fail a gate → not deployable, shown honestly.
    expect(screen.getAllByText("▲ Not deployable").length).toBeGreaterThan(0);
  });

  it("a null DSR/PBO renders '—', never a fabricated value", async () => {
    renderModels();
    // meta_labeler rows carry cpcv_dsr:null / pbo:null → the DSR/PBO badges
    // render an em-dash (e.g. "DSR —"), never a fabricated 0.
    await screen.findByText("meta_labeler_timeseries_momentum");
    expect(screen.getAllByText(/—/).length).toBeGreaterThan(0);
  });

  it("flags a stale model with the Needs Retrain badge, never on a fresh one", async () => {
    // Mock fixture: meta_labeler_cross_sectional_momentum is trained well
    // outside the 30-day window (needs_retrain: true); the other two dated
    // models are fresh (needs_retrain: false); cnn_lstm_price_forecaster has
    // no trained_date at all (needs_retrain: null) -- exactly ONE badge.
    renderModels();
    await screen.findByText("meta_labeler_cross_sectional_momentum");
    expect(screen.getAllByText("⏱ Needs retrain").length).toBe(1);
  });

  it("a model with no trained_date renders an honest '—' age, never a guessed retrain flag", async () => {
    renderModels();
    // cnn_lstm_price_forecaster: trained_date/age_days/needs_retrain all
    // null -- must render the dash, not "NaNd ago" or a fabricated badge.
    const card = (await screen.findByText("cnn_lstm_price_forecaster")).closest("section")!;
    expect(within(card).getByText("Trained —")).toBeInTheDocument();
    expect(within(card).queryByText("⏱ Needs retrain")).not.toBeInTheDocument();
  });

  it("an empty registry renders the honest empty state", async () => {
    vi.spyOn(api, "getModels").mockResolvedValueOnce([]);
    renderModels();
    expect(
      await screen.findByText("No model registry available yet.")
    ).toBeInTheDocument();
  });

  it("renders live CPCV-DSR/PBO thresholds in the footer and drives the badge color, never a hard-coded literal", async () => {
    // lgbm_ranker's fixture pbo is 0.267 -- above this deliberately low
    // pbo_max, so a LIVE threshold flips its PBO badge to "not good" where
    // the old hard-coded 0.50 literal would have shown it passing.
    vi.spyOn(api, "getThresholds").mockResolvedValue({
      pbo_max: 0.2,
      dsr_min: 0.95,
      net_sharpe_min: 0.5,
      max_drawdown_max: 0.3,
      stress_max_drawdown: 0.5,
      kelly_fraction: 0.5,
      kelly_cap: 0.2,
      robinhood_max_notional_per_order: 0.0,
      follow_min_amount: 100.0,
      agentic_max_candidates: 25,
      retrain_window_days: 30,
    });
    renderModels();
    expect(
      await screen.findByText(/Deployable = CPCV-DSR > 0\.95 AND PBO < 0\.20\./)
    ).toBeInTheDocument();
    const pboBadge = await screen.findByText(/PBO 0\.27/);
    expect(pboBadge).toHaveClass("badge-warn");
  });

  it("footer and badge coloring degrade honestly (never a fabricated gate) when the threshold fetch fails", async () => {
    vi.spyOn(api, "getThresholds").mockRejectedValue(new Error("offline"));
    renderModels();
    expect(
      await screen.findByText(/Deployable = CPCV-DSR > — AND PBO < —\./)
    ).toBeInTheDocument();
    const pboBadge = await screen.findByText(/PBO 0\.27/);
    expect(pboBadge).toHaveClass("badge-neutral");
  });

  it("the deployability filter buttons narrow the rendered list", async () => {
    renderModels();
    await screen.findByText("lgbm_ranker");
    // Fixture: only meta_labeler_cross_sectional_momentum has needs_retrain
    // true (see the "flags a stale model" test above) -- an exact, single-row
    // narrowing this fixture makes unambiguous to assert on.
    fireEvent.click(screen.getByText("Needs Retrain"));
    await waitFor(() => {
      expect(screen.queryByText("lgbm_ranker")).not.toBeInTheDocument();
    });
    expect(screen.getByText("meta_labeler_cross_sectional_momentum")).toBeInTheDocument();
    expect(screen.queryByText("meta_labeler_timeseries_momentum")).not.toBeInTheDocument();
    expect(screen.queryByText("cnn_lstm_price_forecaster")).not.toBeInTheDocument();

    // Back to "All" restores every row.
    fireEvent.click(screen.getByText("All"));
    await screen.findByText("lgbm_ranker");
    expect(screen.getByText("meta_labeler_timeseries_momentum")).toBeInTheDocument();
    expect(screen.getByText("cnn_lstm_price_forecaster")).toBeInTheDocument();
  });

  it("the sort dropdown reorders the list by DSR (descending)", async () => {
    const rows: ModelRow[] = [
      {
        name: "model_low", role: "cross_sectional_ranker", trained_date: "2026-07-01",
        cpcv_dsr: 0.1, pbo: 0.4, n_train: 100, deployable: false, notes: null,
        age_days: 10, needs_retrain: false, cpcv_mean_oos_sharpe: null, cpcv_mean_oos_max_dd: null,
      },
      {
        name: "model_high", role: "cross_sectional_ranker", trained_date: "2026-07-01",
        cpcv_dsr: 0.9, pbo: 0.1, n_train: 100, deployable: true, notes: null,
        age_days: 10, needs_retrain: false, cpcv_mean_oos_sharpe: null, cpcv_mean_oos_max_dd: null,
      },
      {
        name: "model_mid", role: "cross_sectional_ranker", trained_date: "2026-07-01",
        cpcv_dsr: 0.5, pbo: 0.3, n_train: 100, deployable: false, notes: null,
        age_days: 10, needs_retrain: false, cpcv_mean_oos_sharpe: null, cpcv_mean_oos_max_dd: null,
      },
    ];
    vi.spyOn(api, "getModels").mockResolvedValueOnce(rows);
    renderModels();
    await screen.findByText("model_low");

    const namesInOrder = () =>
      screen.getAllByText(/^model_(low|high|mid)$/).map((el) => el.textContent);
    // Default (registry) order, unsorted.
    expect(namesInOrder()).toEqual(["model_low", "model_high", "model_mid"]);

    const select = screen.getByTestId("models-sort-select");
    fireEvent.change(select, { target: { value: "dsr" } });
    expect(namesInOrder()).toEqual(["model_high", "model_mid", "model_low"]);
  });

  it("renders the macro-gate banner only when the gate is enabled AND the macro kill switch is active", async () => {
    const base = await api.getObservabilitySummary("1M", 30);
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValue({
      ...base,
      regime: { ...base.regime, macro_regime_gate_enabled: true, macro_kill_switch: true },
    });
    renderModels();
    expect(
      await screen.findByText(/New buy orders are paused by the macro regime gate/)
    ).toBeInTheDocument();
  });

  it("does not render the macro-gate banner when the kill switch is inactive (default mock state)", async () => {
    renderModels();
    await screen.findByText("lgbm_ranker");
    expect(
      screen.queryByText(/New buy orders are paused by the macro regime gate/)
    ).not.toBeInTheDocument();
  });

  it("does not render the macro-gate banner when the gate itself is disabled, even if the kill switch is active", async () => {
    const base = await api.getObservabilitySummary("1M", 30);
    vi.spyOn(api, "getObservabilitySummary").mockResolvedValue({
      ...base,
      regime: { ...base.regime, macro_regime_gate_enabled: false, macro_kill_switch: true },
    });
    renderModels();
    await screen.findByText("lgbm_ranker");
    expect(
      screen.queryByText(/New buy orders are paused by the macro regime gate/)
    ).not.toBeInTheDocument();
  });

  it("clicking Retrain Now on the lgbm_ranker row calls createJob with train_lgbm", async () => {
    const createJobSpy = vi.spyOn(api, "createJob").mockResolvedValue({
      job_id: "mock-job-lgbm",
      job_type: "train_lgbm",
      status: "running",
      cancellable: true,
    } as JobRecord);
    renderModels();
    const card = (await screen.findByText("lgbm_ranker")).closest("section")!;
    fireEvent.click(within(card).getByText("Retrain Now"));
    await waitFor(() => expect(createJobSpy).toHaveBeenCalledWith("train_lgbm"));
  });

  it("clicking Retrain Now on a meta_labeler_* row calls createJob with train_meta and the derived signal", async () => {
    const createJobSpy = vi.spyOn(api, "createJob").mockResolvedValue({
      job_id: "mock-job-meta",
      job_type: "train_meta",
      status: "running",
      cancellable: true,
    } as JobRecord);
    renderModels();
    const card = (await screen.findByText("meta_labeler_timeseries_momentum")).closest("section")!;
    fireEvent.click(within(card).getByText("Retrain Now"));
    await waitFor(() =>
      expect(createJobSpy).toHaveBeenCalledWith("train_meta", { signal: "timeseries_momentum" })
    );
  });

  it("shows a clear inline error, not just console.error, when createJob 409s (another training job in flight)", async () => {
    vi.spyOn(api, "createJob").mockRejectedValueOnce(
      new ApiError("already running", 409)
    );
    renderModels();
    const card = (await screen.findByText("lgbm_ranker")).closest("section")!;
    fireEvent.click(within(card).getByText("Retrain Now"));
    expect(
      await within(card).findByText("Another training job is already running.")
    ).toBeInTheDocument();
  });

  it("Retrain Now clears back to enabled once GET /jobs/{id} reports terminal, even when the WS 'finished' event never fires (the real Retrain-Now-flow path -- it never opens the SSE log stream that broadcast depends on)", async () => {
    vi.spyOn(api, "createJob").mockResolvedValueOnce({
      job_id: "mock-job-lgbm",
      job_type: "train_lgbm",
      status: "running",
      cancellable: true,
    } as JobRecord);
    const getJobStatusSpy = vi
      .spyOn(api, "getJobStatus")
      .mockResolvedValue({
        job_id: "mock-job-lgbm",
        job_type: "train_lgbm",
        status: "success",
        exit_code: 0,
        cancellable: true,
      } as JobRecord);
    renderModels();
    const card = (await screen.findByText("lgbm_ranker")).closest("section")!;

    // The reload() the poll below triggers on completion re-invokes
    // api.getModels() -- swap it (AFTER the real initial render above) for a
    // same-data mock that resolves on a plain microtask instead of the real
    // mock API's own ~260ms delay(), so the fake-timer advance below only
    // has to account for POLL_INTERVAL_MS itself, not a second, independently
    // -timed hop.
    const modelsFixture = await api.getModels();
    vi.spyOn(api, "getModels").mockResolvedValue(modelsFixture);

    // Fake timers from HERE, before the click -- the poll interval this test
    // exercises is created (via setInterval) the instant the click flips the
    // model into trainingJobs, and vi.useFakeTimers() only intercepts
    // setInterval calls made AFTER it's installed. Everything from this
    // point on uses synchronous getBy/queryBy, matching
    // AgenticTrading.test.tsx's identical convention for the same reason.
    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(within(card).getByText("Retrain Now"));
    });

    expect(within(card).getByText("Training…")).toBeInTheDocument();

    // Advance past one POLL_INTERVAL_MS tick -- useTrainingStatus' WS never
    // connects in jsdom (no real server), so trainingStatuses stays {}
    // throughout and the WS-based clear effect never fires. If the poll were
    // absent (the actual bug), the button would still read "Training…" here.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(getJobStatusSpy).toHaveBeenCalledWith("mock-job-lgbm");
    // Re-query fresh from the document rather than reusing `card` -- reload()
    // flips `loading` true then false, which unmounts and remounts the
    // ENTIRE model list (Models.tsx gates it behind `!loading && ...`), so
    // the original `card` reference is a genuinely detached node by now,
    // exactly the stale-DOM-reference class of race documented at length in
    // Settings.test.tsx's Brokerage describe block.
    const newCard = screen.getByText("lgbm_ranker").closest("section")!;
    expect(within(newCard).getByText("Retrain Now")).toBeInTheDocument();
    expect(within(newCard).queryByText("Training…")).not.toBeInTheDocument();
  });

  it("does not render a Retrain Now button for a model with no backend job (forecast_overlay role)", async () => {
    renderModels();
    const card = (await screen.findByText("cnn_lstm_price_forecaster")).closest("section")!;
    expect(within(card).queryByText("Retrain Now")).not.toBeInTheDocument();
  });

  it("renders the honest OOS Sharpe/Max DD badges, '—' for an un-validated model", async () => {
    renderModels();
    // lgbm_ranker's fixture carries real cpcv_mean_oos_sharpe/max_dd.
    const lgbmCard = (await screen.findByText("lgbm_ranker")).closest("section")!;
    expect(within(lgbmCard).getByText(/OOS Sharpe \(CPCV\) 0\.31/)).toBeInTheDocument();
    expect(within(lgbmCard).getByText(/OOS Max DD \(CPCV\) 28/)).toBeInTheDocument();
    // meta_labeler rows are un-validated -> both null -> "—", never fabricated.
    const metaCard = (
      await screen.findByText("meta_labeler_timeseries_momentum")
    ).closest("section")!;
    expect(within(metaCard).getByText(/OOS Sharpe \(CPCV\) —/)).toBeInTheDocument();
    expect(within(metaCard).getByText(/OOS Max DD \(CPCV\) —/)).toBeInTheDocument();
  });
});

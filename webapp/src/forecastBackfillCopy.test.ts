/**
 * forecastBackfillCopy.test.ts — copy helpers for the Agentic Forecast
 * Backfill & Meta-Labeling screen. Covers backfillFailureMessage()'s
 * per-cause branches, including the "timeout with checkpointed partial
 * results" branch added alongside `ForecastBackfillJob.partial_summary`
 * (mirrors `ml/forecast_backfill_job.py`'s `BackfillJobState.partial_summary`).
 */
import { describe, expect, it } from "vitest";
import { backfillFailureMessage, formatBackfillCountdown, PHASE_LABEL } from "./forecastBackfillCopy";
import type { ForecastBackfillJob } from "./api/types";

function job(overrides: Partial<ForecastBackfillJob> = {}): ForecastBackfillJob {
  return {
    job_id: "job-1",
    state: "failed",
    phase: null,
    step: 0,
    total_steps: 7,
    error: null,
    error_type: null,
    summary: null,
    sample_rows: null,
    partial_summary: null,
    seconds_remaining: 0,
    ...overrides,
  };
}

describe("backfillFailureMessage", () => {
  it("returns the generic message when job is null", () => {
    expect(backfillFailureMessage(null)).toBe("The backfill did not complete. Nothing was saved.");
  });

  it("timeout with no partial_summary reports the honest 'nothing was saved' message unchanged", () => {
    const msg = backfillFailureMessage(job({ state: "timeout", error_type: "timeout", partial_summary: null }));
    expect(msg).toBe("The backfill timed out. Nothing was saved.");
  });

  it("timeout with an empty trained list (no progress event ever observed) also reports 'nothing was saved'", () => {
    const msg = backfillFailureMessage(
      job({
        state: "timeout",
        error_type: "timeout",
        partial_summary: { trained: [], metrics_so_far: {} },
      })
    );
    expect(msg).toBe("The backfill timed out. Nothing was saved.");
  });

  it("timeout with a non-empty partial_summary reports the trained count honestly, singular", () => {
    const msg = backfillFailureMessage(
      job({
        state: "timeout",
        error_type: "timeout",
        partial_summary: {
          trained: ["timeseries_momentum_10d"],
          metrics_so_far: {
            timeseries_momentum_10d: { accuracy: 0.52, auc: 0.55, n_train: 100, n_test: 0, split_date: "CPCV", is_active: true },
          },
        },
      })
    );
    expect(msg).toBe("The backfill timed out after training 1 model — partial results were saved.");
  });

  it("timeout with a non-empty partial_summary reports the trained count honestly, plural, computed from the data (not hardcoded)", () => {
    const msg = backfillFailureMessage(
      job({
        state: "timeout",
        error_type: "timeout",
        partial_summary: {
          trained: ["timeseries_momentum_10d", "timeseries_momentum_30d", "rsi2_mean_reversion_10d"],
          metrics_so_far: {
            timeseries_momentum_10d: { accuracy: 0.52, auc: 0.55, n_train: 100, n_test: 0, split_date: "CPCV", is_active: true },
            timeseries_momentum_30d: { accuracy: 0.53, auc: 0.56, n_train: 90, n_test: 0, split_date: "CPCV", is_active: true },
            rsi2_mean_reversion_10d: { accuracy: 0.51, auc: 0.53, n_train: 80, n_test: 0, split_date: "CPCV", is_active: true },
          },
        },
      })
    );
    expect(msg).toBe("The backfill timed out after training 3 models — partial results were saved.");
  });

  it("cancelled state is unaffected by partial_summary", () => {
    expect(backfillFailureMessage(job({ state: "cancelled", error_type: "cancelled" }))).toBe(
      "Backfill cancelled. Nothing was saved."
    );
  });

  it("failed state branches on error_type: value_error", () => {
    const msg = backfillFailureMessage(
      job({ state: "failed", error_type: "value_error", error: "bad theta_c" })
    );
    expect(msg).toBe("The backfill's request parameters were invalid: bad theta_c");
  });

  it("failed state branches on error_type: unexpected", () => {
    const msg = backfillFailureMessage(job({ state: "failed", error_type: "unexpected", error: "boom" }));
    expect(msg).toBe("An unexpected error occurred during training: boom");
  });

  it("failed state with no error message and no recognized error_type falls back to a generic honest message", () => {
    const msg = backfillFailureMessage(job({ state: "failed", error_type: null, error: null }));
    expect(msg).toBe("The backfill failed. Nothing was saved.");
  });
});

describe("formatBackfillCountdown", () => {
  it("formats seconds as m:ss, zero-padded", () => {
    expect(formatBackfillCountdown(65)).toBe("1:05");
    expect(formatBackfillCountdown(0)).toBe("0:00");
    expect(formatBackfillCountdown(-5)).toBe("0:00");
  });
});

describe("PHASE_LABEL", () => {
  it("has a label for every BackfillPhase", () => {
    const phases = [
      "fetching_data",
      "technical_features",
      "primary_signals",
      "meta_targets",
      "backtraining",
      "backfilling",
      "exporting",
    ] as const;
    for (const p of phases) {
      expect(typeof PHASE_LABEL[p]).toBe("string");
      expect(PHASE_LABEL[p].length).toBeGreaterThan(0);
    }
  });
});

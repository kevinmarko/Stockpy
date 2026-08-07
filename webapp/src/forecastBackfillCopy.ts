import type { ForecastBackfillJob, ForecastBackfillPhase } from "./api/types";

export const PHASE_LABEL: Record<ForecastBackfillPhase, string> = {
  fetching_data: "Fetching data…",
  technical_features: "Calculating technical features…",
  primary_signals: "Generating primary signals…",
  meta_targets: "Creating meta targets…",
  backtraining: "Backtraining meta labelers…",
  backfilling: "Executing backfill…",
  exporting: "Exporting results…",
};

export function formatBackfillCountdown(seconds: number): string {
  const clamped = Math.max(0, Math.round(seconds));
  const m = Math.floor(clamped / 60);
  const s = clamped % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * Honest, per-cause failure copy for a terminal `ForecastBackfillJob` --
 * mirrors `brokerageLoginCopy.ts`'s `loginFailureMessage`'s style, branching
 * on the more specific `error_type` where it adds real value over the
 * coarse terminal `state` alone (e.g. distinguishing "the request
 * parameters were invalid" from "an unexpected error occurred mid-training").
 */
export function backfillFailureMessage(job: ForecastBackfillJob | null): string {
  if (!job) return "The backfill did not complete. Nothing was saved.";
  if (job.state === "timeout") {
    return "The backfill timed out. Nothing was saved.";
  }
  if (job.state === "cancelled") {
    return "Backfill cancelled. Nothing was saved.";
  }
  switch (job.error_type) {
    case "value_error":
      return job.error
        ? `The backfill's request parameters were invalid: ${job.error}`
        : "The backfill's request parameters were invalid.";
    case "unexpected":
      return job.error
        ? `An unexpected error occurred during training: ${job.error}`
        : "An unexpected error occurred during training. Nothing was saved.";
    default:
      return job.error || "The backfill failed. Nothing was saved.";
  }
}

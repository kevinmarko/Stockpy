import type { ForecastBackfillJob, ForecastBackfillPhase } from "./api/types";

export const PHASE_LABEL: Record<ForecastBackfillPhase, string> = {
  fetching_data: "Fetching data…",
  technical_features: "Calculating technical features…",
  primary_signals: "Generating primary signals…",
  meta_targets: "Creating meta targets…",
  backtraining: "Backtraining meta labelers…",
  backfilling: "Executing backfill…",
  registry_bridge: "Registering live meta-labelers…",
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
    // ml/forecast_backfill_job.py's _enforce_deadline never touches
    // partial_summary -- it's whatever the last {"event": "progress", ...}
    // checkpoint (after each step-5 combo trains) left behind, or null if
    // the kill landed before any combo finished (steps 1-4). There is no
    // "total planned model count" carried on the job/partial_summary to
    // report an honest denominator against, so this reports the trained
    // count alone rather than fabricating one -- CONSTRAINT #4.
    const trained = job.partial_summary?.trained ?? [];
    if (trained.length > 0) {
      const n = trained.length;
      return `The backfill timed out after training ${n} model${n === 1 ? "" : "s"} — partial results were saved.`;
    }
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

/**
 * Honest, human-readable label for a machine-readable eligibility `reason`
 * from `ml/forecast_backfill.py::_mark_eligibility` (surfaced on
 * `ForecastBackfillSummary.eligibility[<signal>].reason`) -- e.g.
 * "insufficient_samples:0_for_90d" -> "Insufficient training samples (0)
 * at the 90d horizon". Falls back to the raw reason string verbatim for a
 * reason shape this hasn't been taught to parse, rather than hiding it.
 */
export function formatEligibilityReason(reason: string | null): string {
  if (!reason) return "Unknown";
  const insufficientMatch = reason.match(/^insufficient_samples:(\d+)_for_(\d+)d$/);
  if (insufficientMatch) {
    const [, n, h] = insufficientMatch;
    return `Insufficient training samples (${n}) at the ${h}d horizon`;
  }
  if (reason.startsWith("missing_required_features:")) {
    return `Missing required input columns: ${reason.slice("missing_required_features:".length)}`;
  }
  if (reason === "unresolvable_features") {
    return "None of this signal's declared training features could be resolved";
  }
  if (reason.startsWith("compute_error:")) {
    return `Signal computation failed: ${reason.slice("compute_error:".length)}`;
  }
  if (reason === "no_meta_label_features") {
    return "No meta-label features declared";
  }
  return reason;
}

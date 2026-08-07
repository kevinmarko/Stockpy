import type { ForecastBackfillPhase } from "./api/types";

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

export function backfillFailureMessage(job: import("./api/types").ForecastBackfillJob | null): string {
  if (!job) return "The backfill did not complete. Nothing was saved.";
  if (job.state === "timeout") {
    return "The backfill timed out. Nothing was saved.";
  }
  if (job.state === "cancelled") {
    return "Backfill cancelled. Nothing was saved.";
  }
  return job.error || "The backfill failed. Nothing was saved.";
}

import type { FollowResult, Thresholds } from "../api/types";

// Split out of FollowModal.tsx (a pure helper, no React) so that file only
// exports the `FollowModal` component -- keeps Vite's React Fast Refresh
// working there instead of invalidating on every edit.
/**
 * Resolves the minimum follow amount to display/gate on. `result.min_amount`
 * (once a real follow response exists) is the most authoritative source — it
 * may reflect server-side overrides that a cached `GET /thresholds` fetch
 * wouldn't know about — so it always wins when present. Before that, the
 * live `GET /thresholds` value (`follow_min_amount`, read live from
 * `settings.FOLLOW_MIN_AMOUNT`) is used. Never a hardcoded literal: if
 * neither has resolved yet, the minimum is honestly `null` (unknown), not a
 * guessed number — callers render that via `fmtUsd(null)` ("—").
 */
export function resolveMinAmount(
  result: FollowResult | null,
  thresholds: Thresholds | null
): number | null {
  return result?.min_amount ?? thresholds?.follow_min_amount ?? null;
}

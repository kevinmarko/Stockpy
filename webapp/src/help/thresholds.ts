/**
 * thresholds.ts — lazy, cached loader for GET /thresholds.
 *
 * Powers helpContent.ts's live-value glossary entries: rather than re-typing
 * PBO/DSR/Sharpe/Kelly numbers as string literals (which would silently drift
 * from validation/thresholds.py and settings.py the next time an operator
 * tunes a gate), the education panel fetches the real values and interpolates
 * them at render time — mirroring the live-import discipline
 * gui/help_content.py applies for the Streamlit Command Center.
 *
 * Mirrors components/SymbolInput.tsx's `loadUniverse()` pattern: module-level
 * cache + in-flight promise dedup, fetched at most once per session, and
 * non-fatal on failure. Unlike the symbol universe (where an empty list is a
 * harmless "no suggestions" UI state), a failed threshold fetch has no honest
 * numeric fallback to substitute — resolving to `null` lets every consumer
 * render "—" via the existing `fmtNum`/`fmtPct` null-handling (format.ts)
 * rather than guessing a value that might already be stale.
 */
import { api } from "../api/client";
import type { Thresholds } from "../api/types";

let thresholdsCache: Thresholds | null = null;
let thresholdsPromise: Promise<Thresholds | null> | null = null;

export function loadThresholds(): Promise<Thresholds | null> {
  if (thresholdsCache) return Promise.resolve(thresholdsCache);
  if (!thresholdsPromise) {
    thresholdsPromise = api
      .getThresholds()
      .then((t) => {
        thresholdsCache = t;
        return t;
      })
      .catch((err) => {
        console.warn("helpContent: failed to load live thresholds", err);
        thresholdsPromise = null;
        return null;
      });
  }
  return thresholdsPromise;
}

/** Exposed for tests to reset the shared cache between cases. */
export function __resetThresholdsCache() {
  thresholdsCache = null;
  thresholdsPromise = null;
}

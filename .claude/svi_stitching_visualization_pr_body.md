# feat: Google Trends SVI Stitching Demo — consolidate into `/data/trends/stitch-demo`, fix data fabrication

## Goal
Give the live (non-mock) Pilots PWA a real, working `GET /data/trends/stitch-demo` response instead of the
hardcoded `501 Not Implemented` it returns today, by wiring `data/trends_stitcher.py`'s
`GoogleTrendsStitcher` overlapping-window stitching algorithm to a real data source — while never
fabricating a value on a data-fetch failure.

## Background — corrections to this branch's original scope
This branch (`integrate_svi_stitching_ui`) was originally built to add a **standalone HTML/JS/SVG
"Studio Artifact"** (`svi_stitching_visualizer.html`) as an interactive demo of the stitching math, with
its own separate `GET /data/svi-stitching-demo` backend endpoint. A completed 6-agent audit found several
problems with that original scope, all addressed by this PR:

1. **The HTML artifact was never actually delivered.** The original PR body and task tracker claimed it
   as `[x]` done ("Created Studio Artifact: `svi_stitching_visualizer.html`"), but the file was never
   committed to this repo — it only ever existed in an IDE-local scratch directory outside git (confirmed
   as of the current PR body edit too: it still exists only in two `~/.gemini/antigravity/brain/*`
   scratch directories on the machine that authored it, never committed to this repo on any branch, at
   any commit). The branch's own commit history confirms the pivot away from it: none of the branch's
   real commits touch an HTML/JS/SVG file, and one commit message explicitly documents switching to a
   backend API endpoint instead. This PR drops the HTML-artifact claim entirely and ships only the
   backend endpoint.
2. **This branch duplicated an already-shipped feature.** PR #953 (merged one day before this branch's
   work began) had already shipped the real `GET /data/trends/stitch-demo` endpoint plus the
   `webapp/src/screens/TrendsVisualizer.tsx` screen that calls it — but only a mock-mode demo; live mode
   deliberately returns `501` with a message pointing the operator at mock mode. This branch was built in
   ignorance of that merge and added a second, separate route (`/data/svi-stitching-demo`) doing
   conceptually the same thing. This PR **consolidates the two**: the real-data-fetch logic this branch
   built is merged into the existing `/data/trends/stitch-demo` endpoint, and the separate
   `/data/svi-stitching-demo` route is dropped entirely (confirmed absent from the current code — grep
   for `svi-stitching-demo`/`get_svi_stitching_demo` in `api/data_api.py` returns nothing). No webapp
   changes are needed — `TrendsVisualizer.tsx` already calls the endpoint this PR fixes.
3. **A real CONSTRAINT #4 (never fabricate a metric) violation was found and fixed.** The branch's
   endpoint fetched real SPY trading volume via `HistoricalStore` as an explicit stand-in for Google
   Trends SVI data (there's no live Google Trends data source wired into this codebase), which is fine
   and clearly labeled as a proxy — but on any fetch failure (insufficient history, a DB error, etc.) it
   silently fell back to `pd.Series([10.0] * 240)`, a flat, made-up series, and still returned a normal
   `200 OK`. The code's own inline comment claimed the opposite of what it did ("Fallback to an
   un-mocked empty state rather than fabricating data"). Fixed: the consolidated endpoint now raises an
   honest `HTTP 503` on any data-fetch failure instead of ever returning a fabricated series.
4. **A real bug in the SPY-fetch path was found and fixed.** The branch's version stripped the real
   `DatetimeIndex` off the SPY bars (`pd.Series(bars["Volume"].tail(N).values)`), which both loses real
   calendar dates for the "overlap" computation and doesn't match the frontend's `[epoch_ms, value][]`
   timestamp contract. Fixed to keep the real `DatetimeIndex` through the whole pipeline.

## Changes Made
- **`api/data_api.py`**: `GET /data/trends/stitch-demo` (live mode) now fetches real SPY trading volume
  from `HistoricalStore` as an explicitly-labeled proxy for Google Trends SVI ("SPY Volume Proxy" in the
  returned curve names — never presented as real SVI data), slices it into three overlapping ~90-day
  windows, and runs them through `GoogleTrendsStitcher.stitch_intervals` to produce the same
  `raw_curves`/`stitched_curve` response shape the mock-mode path already returns (matches
  `TrendsStitchDemoResponse`/`TrendsCurve` in `webapp/src/api/types.ts` exactly — confirmed no
  `stitch1`/`stitch2` keys anywhere in the response — no webapp changes needed). On insufficient history
  or any other fetch failure, it raises `HTTP 503` — never a fabricated fallback series. The standalone
  `/data/svi-stitching-demo` route from this branch's earlier commits is removed; there is now exactly
  one endpoint for this demo.
- **`data/trends_stitcher.py`**: `GoogleTrendsStitcher.stitch_intervals` and `get_scaling_metadata` were
  tightened so the overlap-window/scaling-factor computation has a single source of truth —
  `stitch_intervals` now reuses `get_scaling_metadata`'s own computed `overlap_dates` rather than
  recomputing the overlap window independently. The two computations previously agreed by construction
  (this was not a live correctness bug), but the duplication was a real drift risk for any future edit to
  one without the other.
- **`docs/signals/google_trends_asvi.md`**: updated to document the live-mode demo endpoint's real-data
  behavior (SPY volume as an explicit SVI proxy, the 503-on-failure contract) and the new
  `get_scaling_metadata` method, so the doc doesn't stay silent about a real, reachable code path.
- **Tests**: added coverage (`tests/test_data_api.py`, `tests/test_trends_stitcher.py`; 73 tests total in
  the two touched files, all passing) for the consolidated endpoint's happy path, its insufficient-history
  path (asserts `503`, never a `200` with fabricated data), a generic-exception path, and
  `GoogleTrendsStitcher.get_scaling_metadata` directly.

## Verification Checklist
- **CONSTRAINT #4 — never fabricate a metric**: the endpoint's only two outcomes on a data problem are
  "don't respond with fake data" (`503`) or "respond with real data, honestly labeled as a proxy." No
  literal fallback series remains anywhere in this path.
- **CONSTRAINT #2 — no lookahead bias**: not applicable to this change. Correction to an earlier version
  of this checklist: `GoogleTrendsStitcher` is *referenced* inside the live pipeline import chain (an
  unused `from data.trends_stitcher import ASVICalculator, GoogleTrendsStitcher` in
  `data/attention_sources.py`, a module `pipeline/production_steps.py`/`main_orchestrator.py` import) —
  but that import is dead code, no method on the class is ever called from that chain, so it carries no
  actual lookahead risk. This unused import predates this PR (added by the separate Google Trends FMP
  ASVI feature) and is not touched by this diff. The only call site this PR's own code introduces is
  `api/data_api.py`'s demo endpoint, which operates on already-materialized historical windows with no
  forward-looking computation, and `ASVICalculator`'s existing causal (`shift(1)`) rolling-median logic
  is untouched by this PR.
- **Single source of truth**: the overlap-window/scaling-factor math now lives in exactly one method
  (`get_scaling_metadata`), not two independently-maintained copies.
- **Branch Workflow Rule 5**: plans/trackers/walkthrough remain scoped and prefixed
  `svi_stitching_visualization_*` under `.claude/`.

## Note on process
This PR's scope was corrected by a completed multi-agent audit of the original branch, which found the
false HTML-artifact deliverable claim, swapped constraint numbers in the original checklist (the original
labeled "never fabricate a metric" as Constraint #2 and "single source of truth" as Constraint #4 — this
codebase's actual convention, confirmed by repo-wide grep, is the reverse: **Constraint #2 = no lookahead
bias, Constraint #4 = never fabricate a metric**), the undisclosed duplicate-feature conflict with PR
#953, and the real fabrication bug described above. That audit has now happened; this PR body reflects
its findings and the resulting fix, verified directly against the current code (not the original,
now-superseded draft).

**2026-08-31 correction:** a subsequent PR-body edit briefly reintroduced the same disproven claims this
section describes (the HTML artifact, the `/data/svi-stitching-demo` route, a `stitch1`/`stitch2` response
shape, and the swapped constraint numbering) — none of that ever landed in the actual code, which has
been re-verified against the current `HEAD` and matches everything stated above. This is the corrected
version.

## Update: second 6-agent audit round (2026-08-31)
A fresh, independently-launched 6-agent audit (one agent per dimension — constraint-compliance,
data-integrity, api-webapp-contract, test-coverage, doc-artifact-honesty, duplication-security-cleanup —
each finding then adversarially re-verified by a second agent against the real checkout) was run against
this PR's current code, explicitly told not to take the first audit's report on faith. It raised 9
findings; all 9 were independently confirmed on re-check (0 refuted), and all 9 are fixed in this update:
a fail-closed try/except gap in the endpoint, a real off-by-one-day timezone bug in the chart (backend
encodes UTC-midnight dates, frontend rendered them in local time), a missing navigation entry point (the
screen was unreachable from anywhere in the app UI), an honesty gap in the screen's own header text
(didn't disclose the SPY-volume-proxy substitution the backend already labels), a mock/live fixture
mismatch, a fabrication-regression gap in the happy-path test (proved by mutation — a fake data series
would have passed undetected), a structural-delegation gap in the SSOT regression test (also proved by
mutation), a missing regression guard for the removed duplicate route, and a minor overclaim in this PR
body's own CONSTRAINT #2 justification. Full detail, evidence, and verification numbers (75 Python tests
passed, 70 webapp tests passed, clean typecheck, clean genuine-bug lint) in
`.claude/svi_stitching_visualization_walkthrough.md`'s "Second audit round" section.

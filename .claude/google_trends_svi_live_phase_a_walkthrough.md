# Google Trends SVI live viewer — Phase A walkthrough (data-integrity fixes)

Plan: `.claude/google_trends_svi_live_implementation_plan.md` §3 Phase A. The operator approved
Phase A on 2026-10-11 and accepted every recommendation in §6: switch the universe with a cap
of 10 per pass, accept the partial-day fix, NaN ASVI warm-up, K = 14, no Phase D, and let the
re-stitch clean up orphans. Phases B/C (API + screen) are **not** in this change.

Known-issue write-up: `docs/known_issues/google_trends_stale_scale_and_partial_day_2026_10.md`.

## What changed, item by item

| Plan item | Where | What |
|---|---|---|
| A1 batches | `data/trends_store.py` | New `TrendsDownloadBatch` table (`trends_download_batches`). `batch_window_id()` builds `"<batch_id>/<start>/<end>"`. New methods `record_batch` (batch + raw rows in one transaction), `latest_batch(complete_only=True)`, `load_batch_windows`, `last_attempts`, `list_terms_with_batches`. Reads never raise. |
| A2 atomic re-stitch | `data/trends_store.py`, daemon | `save_stitched_series(..., replace=True)` deletes and inserts in one transaction. The daemon uses it, and only for complete batches. **No purge script** (Decision 7). |
| A3 partial day | `data/google_trends_client.py` | `_drop_partial_rows()` drops `isPartial` rows (bool or `"True"` string). A window left empty is treated like an empty response. |
| A4 persisted schedule | `desktop/daemon_runtime.py` | `_google_trends_due_terms()` (pure) reads `TrendsStore.last_attempts()`. The in-memory `_last_google_trends_refresh` is gone. |
| A5 settings | daemon | `window_days`/`overlap_days` passed from settings. `plan_windows()` in the client is the one window planner, used by both the fetch loop and the daemon's `windows_expected`. |
| A6 retention | store, daemon, `settings.py`, `shared/env_io.py` | `GOOGLE_TRENDS_RAW_RETENTION_BATCHES` (14, `ge=1`). `prune_raw_batches()` also removes legacy `uuid4` rows once a complete batch exists. |
| A7 ASVI warm-up | `data/trends_stitcher.py` | The future-median fill is removed, so the first 7 rows are NaN. |
| A8 universe | daemon, `settings.py`, `shared/env_io.py` | `_google_trends_universe()` → `compute_tracked_universe()` (cached snapshot, watchlist, discovery, `DEFAULT_TICKERS` fallback). `GOOGLE_TRENDS_MAX_TERMS_PER_PASS` (10, `ge=1`). |
| — | `is_in_cooldown()` in the client | Lets the daemon stop the pass on a 429 cooldown instead of recording every remaining term as failed. |

## Design notes (the "why")

- **Why only complete batches are stitched.** Stitching chains windows pairwise through
  their overlaps. If a middle window is missing, two non-overlapping windows get chained,
  `get_scaling_metadata` sees an empty overlap and falls back to `f = 1.0`, and the result
  silently mixes two different scales. Keeping the previous stitch is the honest choice
  (CONSTRAINT #6: fail closed).
- **Why the window id embeds dates.** It makes ids deterministic and sortable, so
  `load_batch_windows` returns windows in chronological order with a plain `ORDER BY`, and no
  `ALTER TABLE` is needed on a live SQLite DB.
- **Why the schedule keys on attempts, not complete downloads.** A first draft keyed on the
  last *complete* download with a 1-hour retry gap. Self-review caught that a ticker Google
  has no data for would then never complete, sort first forever, and be retried hourly,
  wasting slots and quota. Keying on the last attempt of any status, sorted oldest first,
  makes it round-robin. A term that raises is recorded as a `failed` batch so it also waits
  the interval. The cost: a term hit by a transient 429 cooldown waits one interval (24 h).
- **Why the cap.** The timer thread runs this synchronously. With the 5 s request spacing,
  10 terms × 6 windows is about 5 minutes at best, the same scale as before. An uncapped
  tracked universe would hold the timer thread far longer.
- **Naive UTC datetimes.** SQLite `DateTime` drops the time zone, so the daemon stores and
  compares naive UTC throughout.

## Honesty-audit follow-ups (fixed before merge)

An `honesty-auditor` pass over the first version found these, now fixed and tested:

1. A batch was recorded `complete` before its stitch ran, so a stitch failure left a
   "complete" batch next to the old series. The daemon now stitches first and records
   `complete` only if the stitch produced rows. Otherwise the batch is `failed` with
   `reason="stitch failed: ..."`.
2. A cooldown still open from an earlier pass made the first due term a `failed` attempt it
   never made, pushing it back 24 h. The daemon now checks `is_in_cooldown()` *before* each
   fetch and stops the pass without recording anything.
3. Raw rows of partial/failed batches were never pruned for a term that never completes.
   `prune_raw_batches(term, keep)` now runs after every batch and keeps the newest `keep`
   batches of each kind.
4. The failure reason said "no data" for `window_days <= overlap_days`. It now says so
   explicitly.
5. `save_stitched_series(replace=True, series=[])` would wipe a term. It is now refused
   in the store itself.

Not fixed here (they predate this change, so they are reported to the operator instead):
- The dashboard ASVI has no maximum-age check. If downloads keep failing, an old value
  shows as current. Phase B's viewer will expose batch age; a dashboard max-age rule needs
  its own decision.
- The LSTM endpoint reads a sector-proxy term (`api/pilots_api.py`) that the daemon only
  downloads if it is in the tracked universe. The result is honestly empty, not fabricated.

## Verification run this session

- Targeted: `pytest tests/test_trends_store.py tests/test_google_trends_client.py
  tests/test_google_trends_daemon.py tests/test_trends_stitcher.py
  tests/test_production_steps_google_trends.py` → 83 passed.
- The new warm-up perturbation test was run against the **pre-change** `compute_asvi`: it
  leaks at cutoffs 3, 7, 10 and 40 and is identical at 83, so the test catches the bug it
  is meant to.
- Settings artifacts regenerated: `scripts/measure_settings_census.py --write`,
  `scripts/settings_liveness.py --write`. Their freshness tests pass.
- `make ci`: see the PR description for the result.

## Not verified here (needs the operator's machine)

- Behavior against real Google: no network to Google from this session. After merge and a
  daemon restart, run the SQL in the known-issue doc's "Rollout" section.

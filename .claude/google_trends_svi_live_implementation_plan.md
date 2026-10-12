# Live Google Trends SVI Stitching Viewer — Implementation Plan

**Branch:** `claude/google-trends-svi-live-drw74r` · **Date:** 2026-10-06, revised 2026-10-11 · **Status:** Phases A, B and C implemented on this branch (PR #1114, shipped together per the operator, 2026-10-11). Phase D not planned. Walkthroughs: `.claude/google_trends_svi_live_phase_a_walkthrough.md`, `.claude/google_trends_svi_live_phase_bc_walkthrough.md`.
**Predecessors:** [#953](https://github.com/kevinmarko/Stockpy/pull/953) (mock-only demo; live route returned 501),
a later revision that read `TrendsStore` with an SPY-volume proxy fallback, and
[#1100](https://github.com/kevinmarko/Stockpy/pull/1100) (removed the screen as "a demo showing stale Trends data").

---

## 0. Freeze and policy gates (read first)

| Gate | Applies to | Status |
|---|---|---|
| Step-7 feature freeze: *bug fixes* and *measurement/observability of existing behavior* are allowed | Phase A (data-integrity fixes) | Allowed. These are bug fixes in the existing Trends pipeline. |
| Freeze: *new webapp screens* need the operator's explicit OK | Phase B (API) + Phase C (screen) | **Needs explicit OK.** It is arguably observability of existing behavior, since the pipeline already fetches, stitches and scores this data. It is still a new screen, so it is the operator's call. |
| Freeze: new data sources need an OK. Data-source policy: new features may only use FMP/Yahoo | Phases B/C | Not triggered. The viewer only **reads** what the daemon already persists, so it adds no provider or calls. |
| Same | Phase D (on-demand refresh) | **Needs a separate explicit OK.** It adds calls to an unofficial, rate-limited scraper (`pytrends`). |
| "Everything else" tier: plan before code, branch + PR | All phases (daemon, data layer, API) | This document. |

---

## 1. What exists today (and why #953/#1100 never showed a trustworthy "live" picture)

### 1.1 The live pipeline already exists

```
desktop/daemon_runtime.py::maybe_refresh_google_trends   (timer wake, GOOGLE_TRENDS_ENABLED)
   └─ data/google_trends_client.py::fetch_overlapping_windows   (pytrends; shared limiter/cooldown)
        └─ 6 × 90-day windows, 30-day overlap, covering the last 365 days
   ├─ TrendsStore.insert_raw_window(...)          → raw_trends_downloads
   └─ GoogleTrendsStitcher.stitch_multiple_intervals → TrendsStore.save_stitched_series → stitched_google_trends

pipeline/production_steps.py::_apply_google_trends_asvi   (every pipeline cycle)
   └─ TrendsStore(readonly).get_stitched_series(sym) → ASVICalculator.compute_asvi → last value
        → dashboard column Google_Trends_ASVI (diagnostic; not read by scoring/sizing)

api/pilots_api.py  POST /pilots/ml/lstm-attention-forecast  also reads get_stitched_series
```

### 1.2 Measured state of the operator's live DB (read-only queries via the investyo MCP, 2026-10-06)

| Measurement | Value | What it means |
|---|---|---|
| Terms with data | **6**: AAL, ABR, AGNC, AM, AQN, ARCC | Alphabetically the first six. The rest of the tracked universe has no Trends data, so `Google_Trends_ASVI` is NaN for it. |
| Last download | 2026-10-06 11:13 UTC | **Not stale.** The daemon is fetching. |
| Distinct `window_id`s for AAL | **96** (8,336 rows) | Every refresh writes 6 new windows with fresh `uuid4()` ids, and nothing is ever replaced. 96 windows = 16 refresh passes stacked together. |
| Windows per term | AAL 96 → ARCC 60 (decreasing) | Later terms in each pass fail more often, probably from Google 429s and the cooldown. |
| Refresh passes on 2026-10-06 | 3 (05:36, 08:21, 11:13) | The 24 h throttle (`_last_google_trends_refresh`) lives only in memory, so a daemon restart refetches immediately. Either that or the `.env` interval is lower. To verify. |
| Stitched rows for AAL | 402 days: 366 from today's stitch + **36 orphan days** (2025-08-31 → 2025-10-05) left by older stitches | Each re-stitch overwrites only its own 365 days, so older days keep an **older, incompatible scale**. That is a level step in the stored series. |
| Same calendar day across downloads (AAL, 2026-10-05) | 45 (01:14) → 66 (07:17) → 80 (17:00) → 78 (next day) | Google returns a **partial value for the current day**, and the client drops pytrends' `isPartial` flag. `_apply_google_trends_asvi` uses the **last** value, so the dashboard ASVI is computed from a partial, downward-biased day. |
| Same complete day across downloads (AAL, 2026-10-03) | 78 / 79 / 80 / 81 | Google Trends is a **sample**: repeated downloads disagree by a few points. This is normal, and worth showing rather than hiding. |

The old demo picked `populated_terms[0]` ("AAL"), loaded **all** raw rows and grouped them by `window_id`. It therefore plotted up to 96 overlapping "windows" from 16 different downloads against a stitched curve with an orphaned tail. That looks stale or broken even though the data was current. A live viewer built on the store as it is today would repeat that, so the plan fixes the data first.

### 1.3 Other defects found while reading the code

1. **Window settings are ignored.** The daemon calls `fetch_overlapping_windows(sym, start, end)` without passing `GOOGLE_TRENDS_WINDOW_DAYS` / `GOOGLE_TRENDS_OVERLAP_DAYS`, so the function defaults (90/30) always win. Today the values match, so there is no behavior change yet, but the knobs do nothing.
2. **Wrong universe source.** The daemon ingests `settings.DEFAULT_TICKERS`. CLAUDE.md requires anything needing "the full tracked universe" to go through `build_universe_detailed()` / `compute_tracked_universe()`.
3. **ASVI warm-up lookahead.** `ASVICalculator.compute_asvi` fills the first ~7 rows (before `min_periods`) with the median of the first 84 days, which includes dates *after* t. This has no effect on the dashboard value (always the last row), but a chart of the full ASVI series would show 7 lookahead-contaminated points. CONSTRAINT #4 says those rows should be NaN.

---

## 2. Target: what "live" means here

A read-only **Search Attention (Google Trends)** screen. For each term the daemon has fetched, it shows exactly what the pipeline did with the latest download:

1. **Raw windows.** The 6 windows from the latest *complete* download batch. Each is normalized by Google so its own max is 100. This is the "why stitching is needed" view.
2. **Seams.** A table with one row per adjacent window pair: overlap dates, Σ overlap A, Σ overlap B, scaling factor *f*, and whether the degenerate guard (`f = 1.0`) fired. The values come from `GoogleTrendsStitcher.get_scaling_metadata`, the same function the stitcher uses.
3. **Rescaled windows + stitched curve.** The rescaled windows are overlaid on the persisted stitched series, with the overlaps shaded. This is the "the seams disappear" view, now on real data.
4. **ASVI.** The ASVI series, with the latest value equal to the dashboard's `Google_Trends_ASVI` for that symbol (asserted in a test).
5. **Honesty strip.** Last download time and age, batch status (complete / partial: cooldown / failed), whether the most recent day is partial and was excluded, coverage ("Trends data for 6 of N tracked symbols"), and `GOOGLE_TRENDS_ENABLED`. Disabled, no-data and stale states render as such. **No synthetic or proxy series, ever.** The old SPY-volume proxy fallback is not coming back: it was honestly labeled, but it is not search data.
6. *(Optional, Phase C+)* **Sampling-noise band.** For the last ~30 days, the min–max across the retained downloads. It teaches the reader that Trends values are sampled estimates.

---

## 3. Phases

### Phase A — Trends data-integrity fixes *(allowed under the freeze; PR 1)*

| # | Change | Files | Behavior impact |
|---|---|---|---|
| A1 | **Download batches.** New table `trends_download_batches` (`batch_id`, `query_term`, `started_at`, `finished_at`, `windows_expected`, `windows_fetched`, `status` ∈ {`complete`,`partial`,`failed`}, `reason`). The daemon creates one batch per term per pass. Raw rows get a deterministic `window_id = f"{batch_id}/{window_start}/{window_end}"`, so no `ALTER TABLE` is needed and `create_all` adds the new table. New reads: `latest_batch(term, complete_only=True)`, `load_batch_windows(batch_id)`, `list_terms_with_batches()`. | `data/trends_store.py`, `desktop/daemon_runtime.py` | Storage-only. `load_raw_windows` keeps working. |
| A2 | **Atomic re-stitch.** `save_stitched_series(term, series, stitched_at, replace=True)` deletes the term's rows and inserts the new stitch in **one** transaction. That removes the orphan-scale tail. One-off: an operator-gated script `scripts/trends_purge_orphan_stitched.py --dry-run/--apply` removes the existing orphans (or the next refresh does it). | `data/trends_store.py`, daemon, new script | Changes the stored series for dates older than 365 days only. Dashboard ASVI (last row) is unaffected. |
| A3 | **Drop partial days.** `fetch_overlapping_windows` drops rows where pytrends reports `isPartial=True`, and logs the count. | `data/google_trends_client.py` | **Changes the dashboard ASVI value** (from today's partial day to the last complete day). This is a correctness fix, but it is a numeric change in an existing feature, so it is flagged for the operator (Decision 3). |
| A4 | **Persisted throttle.** `maybe_refresh_google_trends` derives "last refresh" from `max(started_at)` in the batch table instead of only process memory, so restarts no longer cause extra passes and fewer 429s. | `desktop/daemon_runtime.py` | Fewer Google calls. |
| A5 | **Wire the window settings.** Pass `GOOGLE_TRENDS_WINDOW_DAYS` / `GOOGLE_TRENDS_OVERLAP_DAYS` into the fetch. | daemon | None at defaults. |
| A6 | **Raw retention.** Keep the last *K* complete batches per term (new setting `GOOGLE_TRENDS_RAW_RETENTION_BATCHES`, default 14) and prune older ones after a successful batch. *K* ≥ 2 keeps the sampling-noise band possible. | store, daemon, `settings.py`, `shared/env_io.py` ALLOWED_KEYS | Bounds growth. Today the table grows by about 540 rows per term per pass. |
| A7 | **ASVI warm-up → NaN** (Decision 4). Rows before `min_periods` stay NaN instead of a forward-looking median. | `data/trends_stitcher.py` | Warm-up rows only. The dashboard's last value is unchanged. |
| A8 | **Universe** (Decision 2). Either keep `DEFAULT_TICKERS` and document it, or switch to `compute_tracked_universe()` with a per-pass term cap (`GOOGLE_TRENDS_MAX_TERMS_PER_PASS`) plus round-robin, so each pass covers the stalest terms first. | daemon, settings | If switched: more Google calls (spread out by the cap). |

**Tests (Phase A):**
- `tests/test_trends_store.py`: batch lifecycle; latest-complete selection skips partial batches; `replace=True` leaves no orphans; retention keeps exactly *K*; reads on a missing table return `[]`, never raise.
- `tests/test_google_trends_client.py`: `isPartial` rows are dropped, using a fake `TrendReq` returning a frame with `isPartial`.
- `tests/test_google_trends_daemon.py`: one batch per term per pass; a cooldown mid-pass marks the batch `partial` with a reason; the throttle survives a new `OrchestratorDaemon` instance; settings are passed through.
- `tests/test_trends_stitcher.py`: warm-up rows are NaN; **perturbation test** (mutate dates > t, assert ASVI[≤ t] bit-identical) now covering the first 84 rows.
- `tests/test_production_steps_google_trends.py`: the dashboard value equals the last complete-day ASVI.
- Root `conftest.py` already has `_isolate_trends_store_db_in_tests`. The new `trends_download_batches` table lives on the same `Base`/engine, so it is covered too. Add one assertion that a default `TrendsStore()` in tests is in-memory.

### Phase B — Read API *(freeze exception needed; same PR as C)*

**Build order:** `.claude/google_trends_svi_live_phase_bc_task.md` (step-by-step tracker with a check after each step).

**Status:** built 2026-10-11 (see the Phases B/C walkthrough). One design change from the spike: HTTP tests use a file-backed SQLite store, because sync FastAPI endpoints run in a worker thread and an in-memory SQLite DB is private to its connection.

**Where the code goes.**
- New pure module `data/trends_view.py`: `build_terms_view(store, tracked_universe, now=None)` and `build_svi_view(store, term, now=None)`. Both read the store only and catch their own failures, returning `status:"error"` with a reason (CONSTRAINT #6).
- Two thin endpoints in `api/data_api.py` (port 8603, read tier `require_token`, output passed through `_clean_nan`). They open `TrendsStore(readonly=True)`.
- **No network calls.** The daemon's pytrends client and its single shared limiter remain the only path to Google.

**Store read helpers to add to `data/trends_store.py`** (each returns an empty value on error and never raises):
- `latest_batches()`: per term, `{"latest": batch|None, "complete": batch|None}` from one ordered scan. Retention keeps the table small, so a scan is fine.
- `stitched_summary()`: per term, `{days, first_date, last_date, stitched_at}` via `GROUP BY`.
- `get_stitched_rows(term)`: like `get_stitched_series` but also returns `stitched_at`. This lets the viewer tell whether the stored series came from the latest batch.

**`GET /data/trends/terms`** returns `status`, `reason`, a config echo, coverage fields and one row per term:
- Status: `ok | no_data | disabled | error`.
- Config echo: `enabled, window_days, overlap_days, refresh_interval_hours, max_terms_per_pass, retention_batches, stale_after_hours, asvi_lookback_days`.
- Coverage: `tracked_universe_size` and `tracked_with_data`, both `null` if the universe lookup fails. The universe comes from the existing `get_universe()` function in the same file (cached broker snapshot only).
- `terms[]` is the union of terms with batches and terms with stitched rows, sorted. Each row has `term, in_tracked_universe, last_attempt_at, last_attempt_status, last_attempt_reason, last_complete_at, age_hours, stale, stitched_days, last_date, latest_asvi, latest_asvi_date, has_complete_batch`.
- `stale` means age > 2 × `GOOGLE_TRENDS_REFRESH_INTERVAL_HOURS`. One interval late is normal round-robin slack.

**`GET /data/trends/svi?term=AAL`**: the term is upper-cased and validated against `^[A-Z0-9.\-]{1,12}$`. An invalid term returns 422 with `detail` starting `invalid_term`.

A valid term with no data returns **200 with `no_data`, not 404**. Any tracked symbol is a legitimate term the daemon may simply not have reached yet. *This changes the original plan, which said 404.*

The response carries the same config echo plus:
- `status`, one of:
  - `ok`: a complete batch and a stitched series.
  - `stitched_only`: a stored series with no complete batch. This covers pre-batch legacy data and series whose recent downloads were incomplete. The stitched curve and ASVI are shown; raw windows and seams are empty, and a reason explains why.
  - `partial_only`: only partial or failed batches, nothing stitched.
  - `no_data`
  - `disabled`: only when there is also no data. Stored data is still shown when refresh is off, with `enabled:false`.
  - `error`
- `reason`
- `batch`: the latest complete batch, `{batch_id, status, reason, started_at, finished_at, windows_expected, windows_fetched, age_hours, stale}`.
- `latest_attempt`: the same shape, for the newest batch of any status.
- `raw_windows[]`: `{index, start, end, points[[epoch_ms, v]], peak_date, peak_value}`. Built with `GoogleTrendsStitcher.group_raw_windows_into_series(load_batch_windows(id))`.
- `seams[]`: `{index, a_window, b_window, overlap_start, overlap_end, overlap_days, sum_a, sum_b, applied_factor, pair_factor, degenerate_guard}`.
- `rescaled_windows[]`
- `stitched`: `{points, first_date, last_date, days, persisted_at, from_latest_batch}`.
- `recompute_check`: `{compared_days, max_abs_diff, same_dates, matches}`.
- `asvi`: `{points, latest, latest_date, lookback_days}`. Warm-up points are `null`.

**Seam maths: the important subtlety the spike found.** `stitch_multiple_intervals` is a *chain*. Window *k* is scaled against **the stitch so far** (already on window 0's scale), not against raw window *k−1*. So:
- `applied_factor` is the cumulative factor the stitcher actually applied: `get_scaling_metadata(stitched_so_far, window_k)["f"]`. `sum_a` and `sum_b` are its overlap sums.
- `pair_factor` is the plain neighbour ratio Σraw_{k−1}/Σraw_k over their overlap. It is `null` when either sum is ≤ 1e-9, because the ratio is undefined there. It is shown to teach the idea of "how much louder this window is than its neighbour".
- `rescaled_windows[k] = raw_k × applied_factor_k`.
- The walk must call `get_scaling_metadata` and `stitch_intervals` themselves, never re-derive the formula, so the numbers are the pipeline's.

**Recompute check.** Re-stitch the batch's raw windows and compare with the persisted series. `matches` means the date sets are identical and `max_abs_diff < 1e-9`. A mismatch is shown as an integrity warning, never hidden.

**ASVI.** `compute_asvi` on the persisted series. Its `latest` must equal `_apply_google_trends_asvi`'s dashboard value; test this with `abs=0`. The lookback is read from `compute_asvi`'s signature (84 days), never a literal.

**Tests: `tests/test_data_api_trends_svi.py`.**
- Fixture: a real in-memory `TrendsStore` shared by monkeypatching `data.trends_store.TrendsStore` to return it. Patch `data_api.get_universe` and `settings.STATE_API_TOKEN=None`.
- Fill the store the way the daemon does: `record_batch` plus a real stitch saved with `stitched_at == finished_at`.
- **Spike lesson:** the patched factory must also be used for the `readonly=True` constructor. A bare in-memory readonly engine is a *separate, empty* DB, and 9 of the spike's 16 tests failed with "no such table" until that is wired correctly. Assert the fixture is actually hit.
- Cases:
  - every status branch;
  - seam 1 and seam 2 factors against `get_scaling_metadata` (seam 2 against the chained stitch);
  - a degenerate-guard seam (zeroed overlap gives `applied_factor 1.0` and `pair_factor null`);
  - a recompute mismatch after tampering with one stored value;
  - latest ASVI equal to the dashboard value;
  - warm-up point `null`;
  - stored data still shown when disabled;
  - 422 tag;
  - store-open failure giving 200 `error`;
  - auth 401 on both endpoints;
  - terms coverage and stale flags;
  - coverage `null` when the universe lookup raises.

### Phase C — Webapp screen *(freeze exception needed; same PR as B)*

Follow the `new-pwa-screen` skill order: **types → client + mock → screen → route → test**.

- **`webapp/src/api/types.ts`:** `TrendsTermsResponse` and `TrendsSviResponse`, with the status unions above (`ok | stitched_only | partial_only | no_data | disabled | error`). Every field the backend can omit is `| null`.
- **`client.ts`:** `getTrendsTerms()` → `http<TrendsTermsResponse>("/data/trends/terms")`, and `getTrendsSvi(term)` → `http<TrendsSviResponse>(`/data/trends/svi?term=${encodeURIComponent(term)}`)`. `baseFor` already routes `/data/*` to :8603.
- **`mock.ts`:** in parity. Build the mock raw windows by normalizing **one fixed underlying series** per window, so the mock seams are mathematically consistent, unlike #953's random curves. Fixtures, one per honesty branch:
  - `ok`
  - `ok` + stale
  - `ok` + recompute mismatch
  - a degenerate-guard seam
  - `stitched_only`
  - `partial_only`
  - `no_data`
  - `disabled`
  - `error`
- **Screen `webapp/src/screens/TrendsAttention.tsx`** at `/research/trends`:
  - A term picker fed by `/data/trends/terms`, defaulting to the freshest term with `ok`.
  - Honesty strip: last download time and age, batch status, stale badge, coverage "N of M tracked symbols", and refresh on/off.
  - Then, in order:
    1. raw windows chart;
    2. seams table;
    3. rescaled + stitched chart with the overlaps shaded;
    4. ASVI chart with a zero line and a marker on the latest value.
  - A recompute mismatch renders a visible warning.
  - Reuse `Loading`, `EmptyState`, `ErrorState`, `StaleDataNotice` and `useApi`. **No polling**: the data changes at most once per refresh interval.
- **Chart components in `webapp/src/components/charts/`:** `TrendsRawWindowsChart`, `TrendsStitchChart` (rebuilt; #953's version is in git at `9cdcde6^`, and its `formatUtcDate` UTC-date fix and fixed-pixel `ResponsiveContainer` height are worth keeping), `TrendsAsviChart`, `TrendsSeamsTable`. Load the `dataviz` skill before writing chart code.
- **Navigation:** a "Search Attention" card in `ResearchHub.tsx` (order mirrors `NAV_ITEMS`), a `NAV_ITEMS` research entry so the desktop sidebar reaches it, and a Marketplace "Explore" tile if mobile reach is wanted (BottomNav shows only the first 3 items).
- **Help:** a `TAB_HELP.trends` entry plus `GLOSSARY` entries for SVI, ASVI, overlap stitching, applied vs pair factor, partial day and sampling noise. Window and overlap days come from the API response, not literals. Add a `docs/HOW_TO_GUIDE.md` section for the anchor.
- **MCP:** add `/research/trends` to `investyo_mcp_server.py::audit_all_pwa_screens`'s fixed route list.
- **Tests:**
  - screen vitest for every fixture branch: null renders "—", never 0; each reason is shown; a 404/error renders the honest state;
  - chart components render with empty and degenerate data;
  - the ResearchHub card-order invariant test.

### Phase D — On-demand "Refresh now" *(optional; separate explicit OK; PR 3)*

A command-tier endpoint (`FOLLOW_API_TOKEN`) asks the **daemon** to refresh one term (via the Control API, so the daemon's limiter and cooldown stay the only path to Google). It runs as a job (6 windows × ≥ 5 s + backoff, roughly 30 s to minutes), returns 409 while a refresh is running, and is refused with a clear reason during cooldown. **Recommendation: skip for now.** Daily cadence is what ASVI needs, and every extra call raises 429 risk for the pipeline's own fetch.

---

## 4. Documentation (part of the deliverable)

| File | Change |
|---|---|
| `docs/signals/google_trends_asvi.md` | Replace §D "removed" with the live viewer. Document batches, partial-day handling, retention, the warm-up NaN rule, and a "Sampling noise" note. Fix the overlap description (setting vs function default). |
| `docs/known_issues/google_trends_stale_scale_and_partial_day_2026_10.md` + `docs/known_issues/README.md` | **Done in Phase A.** Measured defects, root cause, fix, rollout SQL. |
| `docs/architecture/data-layer.md` | **Done in Phase A** (batches, `window_id` convention, retention). B adds `data/trends_view.py`. |
| `docs/architecture/webapp-and-gui.md` | `/research/trends` screen and the two `/data/trends/*` endpoints. |
| `docs/architecture/testing.md` | New/extended test files. |
| `docs/HOW_TO_GUIDE.md` | "Reading the Search Attention screen" (help anchor target). |
| `CLAUDE.md` / `AGENTS.md` | No change expected (no new rule). Update only if Decision 2 changes a universe rule. |
| `.claude/google_trends_svi_live_phase_a_walkthrough.md` | **Done** for Phase A. B/C get `.claude/google_trends_svi_live_phase_bc_walkthrough.md` and `..._phase_bc_task.md`. |

---

## 5. Verification plan

- **Phase A:** targeted `pytest tests/test_trends_store.py tests/test_google_trends_client.py tests/test_google_trends_daemon.py tests/test_trends_stitcher.py tests/test_production_steps_google_trends.py`, then `make ci` (zero failures). After merge and a daemon restart: read-only DB queries confirming one complete batch per term per refresh interval, zero orphan stitched rows, and latest stored date < today (no partial day).
- **Phase B/C:**
  - `pytest tests/test_data_api_trends_svi.py tests/test_data_api.py` + `make ci`. First confirm the test fixture's shared store is really the one the endpoint reads (see the Phase B spike lesson).
  - `npm run --prefix webapp typecheck`, `npm run --prefix webapp test`, and the `api-parity-reviewer` agent
  - `npm run dev` browser check in **mock** mode for every status shape (console clean)
  - **Live check** against the operator's backend (`VITE_USE_MOCK=false`), which the cloud container cannot reach. Either the operator does it, or it runs via the investyo MCP's `inspect_webapp_screen` / `trace_webapp_network`. A typecheck alone has missed live field mismatches before.

---

## 6. Decisions

**Answered 2026-10-11:** Decisions 2–7 below were accepted as recommended and are implemented in Phase A. That means: switch the universe with a cap of 10, the partial-day fix, NaN warm-up, K = 14, no Phase D, and the re-stitch cleans up orphans (no purge script).

**Answered later on 2026-10-11:**
1. Freeze exception for Phases B/C: **granted** ("do b and c").
2. Merge timing: **ship A + B + C together** in #1114.
3. Mobile reach: **no Marketplace tile**; desktop sidebar + Research hub only.
4. Sampling-noise band: **later** (not built).

Original decision list (kept for the record):

1. Freeze exception for Phases B/C.
2. Universe (A8): switch to the tracked universe, cap 10/pass. **Accepted.**
3. Partial-day fix (A3). **Accepted.**
4. ASVI warm-up NaN (A7). **Accepted.**
5. Raw retention K (A6): 14. **Accepted.**
6. Phase D on-demand refresh: not now. **Accepted.**
7. Orphan cleanup by re-stitch, no script. **Accepted.**

## 7. PR split

1. **Phase A:** draft PR #1114 on this branch (data fixes + docs). CI is green, and it is not merged (open Decision 2).
2. **Phases B + C:** one PR, after open Decision 1. They can go on this branch, so all three ship together, or on a new branch after Phase A merges.
3. **Phase D:** not planned (Decision 6).

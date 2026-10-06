# Live Google Trends SVI Stitching Viewer — Implementation Plan

**Branch:** `claude/google-trends-svi-live-drw74r` · **Date:** 2026-10-06 · **Status:** plan, awaiting operator review
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

### Phase B — Read API *(needs freeze OK; PR 2)*

`api/data_api.py` (port 8603, where heavy pandas/stitcher imports are allowed, same as the old demo), read tier `require_token`:

- `GET /data/trends/terms` returns `{enabled, window_days, overlap_days, refresh_interval_hours, tracked_universe_size, terms: [{term, last_batch_at, age_hours, stale, last_status, windows_fetched, windows_expected, stitched_days}]}`.
- `GET /data/trends/svi?term=AAL&batch=latest` returns:
  - `status`: one of `ok | disabled | no_data | partial_only | error`, with a `reason`
  - `batch {id, started_at, status, partial_day_dropped}`
  - `raw_windows[{start, end, points[[ts, v]], peak_date}]`
  - `seams[{a_window, b_window, overlap_start, overlap_end, overlap_days, sum_a, sum_b, factor, degenerate_guard}]`
  - `rescaled_windows[...]`
  - `stitched {points, persisted_at}`
  - `recompute_check {max_abs_diff, matches}`: the stitch is recomputed from the batch's raw windows and compared with the persisted one, as an integrity signal
  - `asvi {points, latest, latest_date, lookback_days}`
  - `sampling_band` (optional, from retained batches)
- Honesty contract: every missing number is `null`, never `0`, and `_clean_nan` runs on output. Disabled or no data is **200 + status**, not 5xx. An unknown term is 404 with a stable tag. Term input is validated (`^[A-Z0-9.\-]{1,12}$`). A store failure degrades to `status:"error"` (CONSTRAINT #6).
- **No network calls** from the API process. That keeps the single shared pytrends limiter, which lives in the daemon, the only client.
- Tests: `tests/test_data_api_trends_svi.py` covering every status branch, the seams math against `get_scaling_metadata`, the recompute check, NaN → null, the auth tier, the 404/422 tags, and *latest ASVI == `_apply_google_trends_asvi` value* for the same store.

### Phase C — Webapp screen *(needs freeze OK; same PR as B)*

Follows the `new-pwa-screen` skill order: **types → client + mock → screen → route → test**.

- `webapp/src/api/types.ts`: `TrendsTermsResponse`, `TrendsSviResponse` (status union included).
- `client.ts` + `mock.ts` in parity. The mock fixtures must cover **ok, disabled, no_data, partial_only, stale and degenerate-guard seam** shapes. The mock's raw windows are built by normalizing one fixed series per window, so the mock seams are mathematically consistent and not random.
- `webapp/src/screens/TrendsAttention.tsx` at route `/research/trends`, plus a card in `ResearchHub.tsx` ("Search Attention").
- Components in `webapp/src/components/charts/`:
  - `TrendsRawWindowsChart`
  - `TrendsStitchChart` (rescaled + stitched, shaded overlaps)
  - `TrendsAsviChart` (zero line, latest marker)
  - `TrendsSeamsTable`
  - Recharts is already a dependency; load the `dataviz` skill before writing chart code.
- Help: a `TAB_HELP.trends` entry plus `GLOSSARY` entries (SVI, ASVI, overlap stitching, partial day, sampling noise). Window and overlap days come from the API response, **not literals**. Add a `docs/HOW_TO_GUIDE.md` section for the anchor.
- Tests: screen vitest for each status shape; chart components render with empty and degenerate data.
- Also update the MCP `audit_all_pwa_screens` route list (#1100 made it a fixed list).

### Phase D — On-demand "Refresh now" *(optional; separate explicit OK; PR 3)*

A command-tier endpoint (`FOLLOW_API_TOKEN`) asks the **daemon** to refresh one term (via the Control API, so the daemon's limiter and cooldown stay the only path to Google). It runs as a job (6 windows × ≥ 5 s + backoff, roughly 30 s to minutes), returns 409 while a refresh is running, and is refused with a clear reason during cooldown. **Recommendation: skip for now.** Daily cadence is what ASVI needs, and every extra call raises 429 risk for the pipeline's own fetch.

---

## 4. Documentation (part of the deliverable)

| File | Change |
|---|---|
| `docs/signals/google_trends_asvi.md` | Replace §D "removed" with the live viewer. Document batches, partial-day handling, retention, the warm-up NaN rule, and a "Sampling noise" note. Fix the overlap description (setting vs function default). |
| `docs/known_issues/google_trends_raw_window_accumulation_2026_10.md` (new) + `docs/known_issues/README.md` | The measured defects from §1.2/§1.3: root cause, fix, status. |
| `docs/architecture/data-layer.md` | `trends_download_batches`, the `window_id` convention, retention. |
| `docs/architecture/webapp-and-gui.md` | `/research/trends` screen and the two `/data/trends/*` endpoints. |
| `docs/architecture/testing.md` | New/extended test files. |
| `docs/HOW_TO_GUIDE.md` | "Reading the Search Attention screen" (help anchor target). |
| `CLAUDE.md` / `AGENTS.md` | No change expected (no new rule). Update only if Decision 2 changes a universe rule. |
| `.claude/google_trends_svi_live_task.md`, `.claude/google_trends_svi_live_walkthrough.md` | Task tracker and walkthrough, written at PR time. |

---

## 5. Verification plan

- **Phase A:** targeted `pytest tests/test_trends_store.py tests/test_google_trends_client.py tests/test_google_trends_daemon.py tests/test_trends_stitcher.py tests/test_production_steps_google_trends.py`, then `make ci` (zero failures). After merge and a daemon restart: read-only DB queries confirming one complete batch per term per refresh interval, zero orphan stitched rows, and latest stored date < today (no partial day).
- **Phase B/C:**
  - `pytest tests/test_data_api_trends_svi.py tests/test_data_api.py` + `make ci`
  - `npm run --prefix webapp typecheck`, `npm run --prefix webapp test`, and the `api-parity-reviewer` agent
  - `npm run dev` browser check in **mock** mode for every status shape (console clean)
  - **Live check** against the operator's backend (`VITE_USE_MOCK=false`), which the cloud container cannot reach. Either the operator does it, or it runs via the investyo MCP's `inspect_webapp_screen` / `trace_webapp_network`. A typecheck alone has missed live field mismatches before.

---

## 6. Decisions needed from the operator

1. **Freeze exception for Phases B/C** (the new screen). Phase A proceeds either way as bug fixes.
2. **Universe (A8):** keep `DEFAULT_TICKERS` (6 terms today), or switch to the tracked universe with a per-pass cap + round-robin. *Recommendation: switch, cap 10/pass.*
3. **Partial-day fix (A3)** changes today's dashboard ASVI value. *Recommendation: accept; the current value is computed from a partial day.*
4. **ASVI warm-up NaN (A7).** *Recommendation: accept; it only affects the first 7 rows of the series.*
5. **Raw retention K (A6).** *Recommendation: 14 batches.*
6. **Phase D on-demand refresh.** *Recommendation: not now.*
7. **Orphan cleanup:** run the one-off purge script, or let the first `replace=True` re-stitch clean it. *Recommendation: let the re-stitch do it; the script is only for the dry-run report.*

## 7. PR split

1. **PR 1: Phase A** (+ known-issue doc). Freeze-allowed.
2. **PR 2: Phases B + C** after Decision 1.
3. **PR 3: Phase D** only if Decision 6 is yes.

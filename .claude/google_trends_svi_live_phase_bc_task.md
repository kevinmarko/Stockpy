# Google Trends live viewer — Phases B + C build plan (task tracker)

**Design source:** `.claude/google_trends_svi_live_implementation_plan.md` §3, Phases B and C (payloads, seam maths, statuses). This file is the **build order**: what to do, in which file, and the check that must pass before the next step. Tick boxes as you go.

**Status (2026-10-11):** done. Built by 6 agents in 3 waves against `.claude/google_trends_svi_live_phase_bc_contract.md` and reviewed by the lead. Evidence: `.claude/google_trends_svi_live_phase_bc_walkthrough.md`. Boxes below are left unticked as the original checklist; the walkthrough records what was verified.

---

## Step 0 — Gates before any code

- [ ] **Operator OK for the freeze exception** (plan §6 open decision 1). A new webapp screen needs it. Without it, stop here.
- [ ] **Branch choice** (plan §6 open decision 2):
  - (a) build on `claude/google-trends-svi-live-drw74r`, so A + B + C merge together in #1114; or
  - (b) merge Phase A first, then a new branch for B + C.
  Default: **(a)**, which is what the operator asked for earlier ("merge it with B and C").
- [ ] `git fetch origin && git rebase origin/main` (or merge main, if the PR has reviewers) and re-run the Phase A targeted tests to confirm a clean start.
- [ ] Load the `new-pwa-screen`, `dataviz` and `stockpy-quant-integrity` skills.

Rough size: B is ~350 lines of Python + ~300 lines of tests. C is ~900 lines of TS/TSX + ~400 lines of tests. Plan on 2 sessions.

---

## Phase B — Read API

### B1. Store read helpers (`data/trends_store.py`)
- [ ] `latest_batches() -> {term: {"latest": dict|None, "complete": dict|None}}`: one `ORDER BY started_at DESC` scan.
- [ ] `stitched_summary() -> {term: {days, first_date, last_date, stitched_at}}`: one `GROUP BY` query.
- [ ] `get_stitched_rows(term) -> [{date, value, stitched_at}]`
- [ ] All three return `{}`/`[]` on any exception and log a warning (same pattern as `latest_batch`).
- [ ] Tests in `tests/test_trends_store.py`: happy path for each, plus a "Session raises" case for each.
- **Gate:** `pytest tests/test_trends_store.py` green.

### B2. View builders (new `data/trends_view.py`)
- [ ] Constants:
  - `TERM_RE = ^[A-Z0-9.\-]{1,12}$`
  - `STALE_INTERVALS = 2.0`
  - `ASVI_LOOKBACK_DAYS`, read from `compute_asvi`'s signature, not a literal
- [ ] `_config()`: the settings echo (enabled, window/overlap days, interval, cap, retention, `stale_after_hours`, `asvi_lookback_days`).
- [ ] `_chain(windows)`: walk the chain with `get_scaling_metadata` + `stitch_intervals` themselves.
  - Each seam carries: `applied_factor` (cumulative, vs the stitch so far), `pair_factor` (raw neighbour ratio, `None` if either sum ≤ 1e-9), `sum_a/sum_b`, overlap dates/days and `degenerate_guard`.
  - Returns seams, rescaled windows and the recomputed stitch.
- [ ] `build_svi_view(store, term, now=None)` sets one status:
  - `no_data` / `disabled`: no batch and no stitched rows; `disabled` only when refresh is off.
  - `partial_only`: batches, but no complete one and nothing stitched.
  - `stitched_only`: stitched rows, but no complete batch (legacy data). Return the stitched series and ASVI; raw windows and seams are empty.
  - `ok`: build raw windows via `group_raw_windows_into_series(load_batch_windows(id))`, then seams, rescaled windows and the recompute check (`matches` = same dates and `max_abs_diff < 1e-9`).
  - `error`: any exception, with a reason naming the exception type and **no message text** (the message could hold paths).
- [ ] `build_terms_view(store, tracked_universe, now=None)`: one row per term (union of batch terms and stitched terms), coverage fields `null` when the universe is `None`, and `stale` when age > 2 × the interval.
- [ ] Points are `[epoch_ms_utc_midnight, value|None]`; datetimes are ISO with `Z`; dates are ISO.
- **Gate:** `python -c "import data.trends_view"` works, and the module imports nothing from `api/`.

### B3. Endpoints (`api/data_api.py`, next to `get_universe`)
- [ ] `GET /data/trends/terms`, `require_token`. Coverage universe from `get_universe()["effective_symbols"]`, in a try (`None` on failure). Store opened with `TrendsStore(readonly=True)`, also in a try (→ `status:"error"`). Output through `_clean_nan`.
- [ ] `GET /data/trends/svi?term=`, `require_token`. Upper-case and strip the term; a `TERM_RE` miss → `HTTPException(422, "invalid_term: …")`. A valid term with no data returns **200** `no_data`, not 404.
- [ ] No pytrends import anywhere on this path. Add a test that asserts `pytrends` is not in `sys.modules` after the calls, when it was not loaded before.
- **Gate:** start the API and run `curl -s localhost:8603/data/trends/terms | jq .status` against the real local DB (read-only). On the operator's machine this is a live shape check; in a cloud session use an in-memory store.

### B4. Tests (new `tests/test_data_api_trends_svi.py`)
- [ ] **Fixture first, and prove it is used** (spike lesson). Monkeypatch `data.trends_store.TrendsStore` to a factory returning one shared in-memory store **for every constructor call, including `readonly=True`**. Add a sanity test that writes a row and reads it back through the endpoint before anything else.
- [ ] Fill the store the way the daemon does: `record_batch` + a real stitch saved with `stitched_at == finished_at`.
- [ ] Cases:
  - every status (`ok`, `stitched_only`, `partial_only`, `no_data`, `disabled`, store-open `error`, builder `error`);
  - seam 1 and seam 2 `applied_factor` against `get_scaling_metadata` (seam 2 against the chained stitch);
  - a degenerate seam (zeroed overlap → `applied_factor == 1.0`, `pair_factor is None`);
  - a recompute mismatch after tampering with one stored value;
  - `asvi.latest == _apply_google_trends_asvi` value with `abs=0`;
  - the first ASVI point is `null`;
  - stored data still shown with `enabled:false`;
  - 422 `invalid_term`;
  - 401 on both endpoints with a wrong token;
  - terms: stale flag, coverage counts, coverage `null` when `get_universe` raises.
- **Gate:** `pytest tests/test_data_api_trends_svi.py tests/test_data_api.py tests/test_trends_store.py` green, then `make ci` green.
- [ ] Commit: "Trends viewer API: /data/trends/terms and /data/trends/svi".

---

## Phase C — Webapp screen

### C1. Types (`webapp/src/api/types.ts`)
- [ ] `TrendsStatus = "ok" | "stitched_only" | "partial_only" | "no_data" | "disabled" | "error"`.
- [ ] `TrendsConfig`, `TrendsBatch`, `TrendsTermRow`, `TrendsTermsResponse`, `TrendsRawWindow`, `TrendsSeam`, `TrendsStitched`, `TrendsRecomputeCheck`, `TrendsAsvi`, `TrendsSviResponse`.
- [ ] Every field the backend can omit is `| null`, and every rendered field is declared explicitly (no index signatures).
- **Gate:** `npm run --prefix webapp typecheck`.

### C2. Client + mock (`client.ts`, `mock.ts`)
- [ ] `getTrendsTerms: () => http<TrendsTermsResponse>("/data/trends/terms")`.
- [ ] `getTrendsSvi: (term) => http<TrendsSviResponse>(`/data/trends/svi?term=${encodeURIComponent(term)}`)`. Use explicit generics (`baseFor` already routes `/data/*` to :8603).
- [ ] Mock: generate raw windows by **normalizing one fixed underlying series per window** (each window divided by its own max × 100). Then compute seams with a small TS port of the chain, so the mock numbers are mathematically consistent. No `Math.random`.
- [ ] Mock terms, one per honesty branch: `AAL` ok, `ABR` ok + stale, `AGNC` stitched_only, `AM` ok + recompute mismatch, `AQN` ok + degenerate seam, `ARCC` partial_only, any other term → `no_data`. Also a mock switch for `disabled`/`error` if the existing mock has a pattern for that (check `mock.ts` first).
- **Gate:** typecheck, then run the `api-parity-reviewer` agent on the diff.

### C3. Chart components (flat in `webapp/src/components/`, importing `./charts` for `chartAxisTick`/`chartAxisLine`/`chartTooltipStyle`)
- [ ] `TrendsRawWindowsChart.tsx`: one line per raw window, each on its own 0–100 scale; legend names the window dates.
- [ ] `TrendsStitchChart.tsx`: rescaled windows (thin, muted) + the stitched series (bold), with overlap spans shaded (`ReferenceArea`).
  - Reuse #953's `formatUtcDate` (UTC so US viewers don't see dates shifted a day) and its fixed-pixel `ResponsiveContainer` height (a percentage height renders 0×0). Get it with `git show 9cdcde6^:webapp/src/components/charts/TrendsStitchChart.tsx`.
- [ ] `TrendsAsviChart.tsx`: ASVI line, `ReferenceLine y=0`, a marker on the latest value, and `connectNulls={false}` so the warm-up gap stays visible.
- [ ] `TrendsSeamsTable.tsx`: seam #, overlap dates and days, Σ A, Σ B, applied factor, pair factor ("—" when null), a guard badge.
- [ ] Each component renders an empty state for empty input, without crashing.
- **Gate:** a component test per file (empty input, degenerate seam, null points).

### C4. Screen (`webapp/src/screens/TrendsAttention.tsx`)
- [ ] `useApi(getTrendsTerms)`, then a term `<select>` defaulting to the freshest `ok` term, then `useApi(() => getTrendsSvi(term), [term])`. **No polling.**
- [ ] Honesty strip: last download time and age, batch status, stale badge, "Trends data for N of M tracked symbols" ("—" when null), refresh on/off.
- [ ] Sections in order: raw windows, then seams table, then stitch chart, then ASVI chart.
- [ ] A recompute mismatch shows a visible warning card.
- [ ] Each non-`ok` status shows its `reason` via `EmptyState`; errors go through `ErrorState`.
- [ ] Reuse `Loading`, `EmptyState`, `ErrorState`, `StaleDataNotice` and `TabGuide`.
- **Gate:** `TrendsAttention.test.tsx` covers every mock branch: null renders "—", never 0 or NaN; each reason text is shown; the mismatch warning shows.

### C5. Route, navigation, help
- [ ] `App.tsx`: `<Route path="/research/trends" element={<TrendsAttention />} />`.
- [ ] `navigation.tsx` `NAV_ITEMS`: a research-section entry (desktop sidebar).
- [ ] `ResearchHub.tsx` `CARDS`: a "Search Attention" card in the same order as `NAV_ITEMS`. Update `ResearchHub.test.tsx`'s order invariant.
- [ ] Marketplace "Explore" tile: only if open decision 3 says so.
- [ ] `help/helpContent.ts`:
  - a `TAB_HELP.trends` entry;
  - `GLOSSARY` entries: SVI, ASVI, overlap stitching, applied factor, pair factor, partial day, sampling noise.
  - Window and overlap days are not literals in help text; read them from the response, or say "each window" generically.
- [ ] `docs/HOW_TO_GUIDE.md`: a "Reading the Search Attention screen" section. If a `shared/help_content.py` anchor is added, `tests/test_help_content.py` must pass.
- [ ] `investyo_mcp_server.py::audit_all_pwa_screens`: add `/research/trends` to the fixed route list.
- **Gate:** `npm run --prefix webapp typecheck && npm run --prefix webapp test && npm run --prefix webapp build`, all green.

### C6. Browser verification (CLAUDE.md: mandatory for UI changes)
- [ ] `npm run dev` (mock mode): open `/research/trends` and step through every mock term. The console must have no errors. Check each chart renders, the shading lines up with the seams table's overlap dates, and the latest ASVI marker matches the strip value.
- [ ] Phone-width check (≤ 400 px): the table scrolls horizontally inside its card, not the page.
- [ ] **Live check** with `VITE_USE_MOCK=false` against the operator's backend (or the investyo MCP `inspect_webapp_screen` / `trace_webapp_network`). Confirm the real payload matches the TS types. A typecheck has missed live field mismatches before.

---

## Step D — Docs, PR, merge

- [ ] `docs/signals/google_trends_asvi.md` §D: replace "removed" with the live viewer description and a "Sampling noise" note.
- [ ] `docs/architecture/data-layer.md`: `data/trends_view.py` entry.
- [ ] `docs/architecture/webapp-and-gui.md`: the screen and both endpoints.
- [ ] `docs/architecture/testing.md`: the new test files.
- [ ] `.claude/google_trends_svi_live_phase_bc_walkthrough.md`: what was built, the design choices (applied vs pair factor, no 404, no polling), and the verification evidence.
- [ ] `make ci` green; webapp typecheck/test/build green; browser check done.
- [ ] Update the #1114 title and description (A + B + C), mark ready, and merge after the operator's go-ahead (it changes dashboard ASVI values via Phase A). After merge, sync main (CLAUDE.md rule 7).
- [ ] After the daemon restart on the operator's machine: run the rollout SQL from `docs/known_issues/google_trends_stale_scale_and_partial_day_2026_10.md`, then open `/research/trends` live.

---

## Risks to watch

| Risk | Mitigation |
|---|---|
| Test fixture silently reads an empty in-memory DB (happened in the spike) | B4's first test proves the shared store is used |
| Live payload drifts from the TS types | C6 live check + `api-parity-reviewer` |
| The 6 legacy terms show `stitched_only` until their first post-Phase-A download | Expected. The reason text explains it, and it clears within one refresh interval after the daemon restart |
| Chart dates off by one day for US viewers | `formatUtcDate` (UTC formatting) |
| Screen reads as live "signal" advice | Copy says diagnostic and advisory-only; ASVI is a dashboard diagnostic column, not a sized signal |

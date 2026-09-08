# Explain This Ticker — Walkthrough (6-agent audit pass, 2026-09-07)

## Context

`bright_quasar_rises_14h56` (an Antigravity build, branched from `main` at `e359775a`)
implemented "Explain This Ticker" plus a bonus "Universe Transparency" screen against
`explain-ticker_implementation_plan.md`/`_task.md` in this same directory. That branch
was merged into this session's working branch, then audited and fixed by a team of
6 parallel Claude Code agents, each owning a disjoint set of files to avoid concurrent-edit
collisions, followed by a final synthesis pass (this document + the fixes it lists in its
own name).

**Bottom line: this was a productive audit, not a rubber stamp.** Of the 6 agents, 5 found
and fixed real, confirmed bugs — including one that would have made a whole panel section
permanently non-functional on every real deployment, and one live-reproduced safety hazard
(an unconditional call to an endpoint that can trigger a real Robinhood login). See
`docs/known_issues/daily_signals_missing_table.md` for the pre-existing context that made
the first of those findable at all.

## What was pre-identified before dispatching the team

1. No `FMP_PROFILE_ENABLED` entry in `.env.example` (plan §9 requires it) — confirmed missing.
2. No new §10 in `docs/FMP_INTEGRATION.md` (plan §9 requires it) — confirmed missing.
3. A genuine, disclosed collision: this branch built a brand-new top-level `/universe`
   screen (`UniverseTransparency.tsx`) before knowing `main` had already shipped its own,
   separately-audited "Universe Transparency panel" (`UniverseCoverage.tsx`, PR #1021).
4. The `.claude/` PR artifacts this branch committed (`explain-ticker_original_request.md`,
   `_project.md`, `_prompt_draft.md`) didn't satisfy CLAUDE.md's required
   `_implementation_plan.md`/`_task.md`/`_walkthrough.md` naming — fixed by copying the real
   plan/task docs into this directory (this file included) before starting the audit.
5. "Invert The Default Search" (the companion plan pair also handed to this session) was
   **never implemented** in this branch — confirmed by diff, zero touches to `SymbolInput.tsx`
   or `Marketplace.tsx`. See `.claude/invert-default-search_walkthrough.md`.

## Per-agent findings

### Agent 1 — `api-parity-reviewer` (read-only: `client.ts`/`mock.ts`/`types.ts`)

- **HIGH**: `ExplainTickerDrawer.tsx` hardcoded reads of `volatility_regime.regime` and
  `sentiment.aggregate_score` didn't match the endpoint's original field names
  (`hmm_risk_on_probability`/`macro_status`, `news_sentiment`/`credibility_weighted_sentiment`)
  — these two tiles would render `"—"` forever against live data.
- **MEDIUM**: `mock.ts`'s 4 `factor_breakdown` fixtures (AAPL/NVDA/XOM/generic fallback)
  used fully fabricated field names for `multifactor`/`momentum`/`volatility_regime`/
  `tactical`/`sentiment` that no real cycle would ever produce.
- **LOW**: the untracked-symbol mock (XYZ) used `coverage_status: "uncovered"` instead of
  the real literal `"untracked"` — wrong badge color in mock-mode preview.
- **LOW**: mock's `company_profile.reason` strings were more specific/informative than the
  real endpoint's single generic message (left as-is; cosmetic, not misleading).
- Confirmed clean: `client.ts`'s `getExplainTicker`, `types.ts`'s `ExplainCompanyProfile`/
  `ExplainTracking`/`ExplainPriceHistoryStatus`, and the entire Universe Transparency
  type/mock contract (`UniverseTransparency.tsx` and `UniverseCoverage.tsx` read identical,
  correct fields — no divergence).

**Resolution** (final synthesis): the HIGH finding was independently resolved by Agent 2's
backend fix (below) via the opposite direction — the backend now genuinely emits
`volatility_regime.regime`/`sentiment.aggregate_score` — but `types.ts` was still tightened
from a loose `Record<string, ...>` to precise interfaces for those two fields specifically
(they're the ones accessed by fixed property name in the drawer; `multifactor`/`momentum`
stay loose since they're rendered generically via `Object.entries()`), so this exact bug
class is a compile error if it recurs. The MEDIUM and first LOW findings were fixed directly
in `mock.ts` in the final synthesis pass.

### Agent 2 — Backend endpoint (`api/data_api.py`'s `GET /data/explain/{symbol}`)

- **Confirmed serious, fixed**: the `factor_breakdown` section queried the `DailySignals`
  SQLite table directly. Per `docs/known_issues/daily_signals_missing_table.md`, that table
  is structurally, permanently empty — no live code path anywhere in this repo ever writes
  a row into it. **Every real deployment would have rendered "no signals computed this cycle"
  forever, regardless of pipeline health.** Fixed by reading `output/state_snapshot.json` via
  `pilots.scoring.load_snapshot()` + `pilots.symbols.find_signal()` — the exact same
  persisted, per-cycle read `pilots/symbols.py`'s Symbol Detail page and
  `pilots/radar_ranking.py`'s Today's Radar feed already use. A genuine read, never a
  recomputation. In the same fix, the section's field names were aligned to what
  `ExplainTickerDrawer.tsx` actually reads (`volatility_regime.regime` ← `macro_status`,
  `sentiment.aggregate_score` ← `news_sentiment`), and a `low_vol_z` key-name mismatch
  against the snapshot's real `lowvol_z` key was corrected.
- **Confirmed CONSTRAINT #6 violation, fixed**: `tracking_section`'s quantity/avg_cost/
  market_value coercion used bare `float(...)` with no guard — a non-numeric field in a
  corrupted snapshot raised an uncaught exception straight out of the endpoint (HTTP 500),
  violating its own "always returns 200" contract. A pre-existing test had actually
  *documented the crash as a known "vulnerability"* rather than fixing it. Fixed by routing
  through `pilots.scoring._coerce_float` (already used elsewhere in this codebase for
  exactly this purpose); the test now asserts graceful `None`-degradation instead of
  asserting the crash.
- Removed the now-dead `_query_daily_signals()` and its unused imports.
- Confirmed clean: `company_profile` (byte-identical unavailable messaging for flag-off vs.
  fetch-failure), `tracking` (genuine `build_sync_report()` reuse, correct field names,
  honest `"untracked"` reporting), `price_history_status` (honest `ok`/`no_data`/`stale`,
  no fabricated line across a gap), auth tier (fail-open read, matches sibling endpoints and
  the `pilots-endpoint` skill convention).
- One disclosed, deliberately-left-alone item: `build_sync_report(probe_market=False)`
  means `coverage_status` reports `"unknown"` rather than a live-probed value for
  performance reasons — an honest enum value, not a fabrication, and a pre-existing
  characteristic of the reused function, out of this endpoint's scope to change.

### Agent 3 — FMP profile wrapper + tooling

- No logic bugs found in `data/fmp_client.py::company_profile()` — already correctly gated,
  never raises, zero network calls on flag-off.
- Filled the two confirmed-missing gaps: added `FMP_PROFILE_ENABLED` to `.env.example`, and
  wrote `docs/FMP_INTEGRATION.md` §10 mirroring §7/§8/§9's structure.
- Investigated but left honestly unresolved: the `mktCap`/`marketCap` field-name ambiguity
  for FMP's `/profile` endpoint on the `stable` API family (vs. the legacy `/v3` family) —
  cannot be verified without a live key in this sandbox. Documented the ambiguity explicitly
  in `settings.py`'s field description and the new docs §10 rather than guessing; confirmed
  `api/data_api.py`'s consumer already defensively reads both spellings.
- Flagged (not fixed, out of scope): `docs/settings_field_census.json`/`.md` and
  `docs/settings_liveness.json` had gone stale the moment `FMP_PROFILE_ENABLED` was added.
  **Regenerated in the final synthesis pass** via
  `python3 scripts/measure_settings_census.py --write` and
  `python3 scripts/settings_liveness.py --write`.

### Agent 4 — Drawer component (`ExplainTickerDrawer.tsx`, `ExplainTickerButton.tsx`, `ExplainTickerContext.tsx`)

- **Confirmed CONSTRAINT #4 violation, fixed**: the recharts `<AreaChart>` fed a raw bars
  array straight through with no gap handling — a genuine multi-week bars gap would render
  as a silently interpolated straight line between two far-apart real points, exactly the
  fabrication the plan's own checklist calls out. Fixed via `buildGapAwareSeries()` (a pure,
  exported, unit-tested function) that inserts an explicit `Close: null` marker across any
  gap wider than 5 calendar days, paired with `connectNulls={false}` and an honest
  "Data gap detected" banner. Also added: a `status: "stale"` surface (previously never
  shown to the user — a stale price history rendered pixel-identical to fresh data), and
  a defensive fallback for `status: "no_data"` with an inconsistent `available: true`.
- Confirmed clean: company-profile honest-unavailable messaging, the three-state tracking
  distinction (held / watchlist-only / untracked — added regression tests proving they stay
  mutually distinct), factor-breakdown null-guards (zero `?? 0`/`|| 0` fabricated-zero
  patterns anywhere in the file), no synthesized cross-section score, and distinct
  loading/error/empty states.
- Flagged (not fixed, informational): the mock fixture set has no bars-gap or `"stale"`
  scenario for `getExplainTicker`/`getDataBars`, so this agent's new tests mock the API
  functions directly rather than relying on `mock.ts` — a nice-to-have follow-up for a
  future session, not a bug.

### Agent 5 — Cross-screen wiring + **live browser click-through**

Per the plan's own explicit lesson (a prior "re-verified, every one matches" claim in this
exact repo was later found wrong for 4 of 8 screens — only a real, non-mocked render caught
it), this agent was required to actually click through every screen rather than grep for
wiring. It did:

- **All 6 screens verified live** against a real running dev server, using real mock data:
  `Portfolio.tsx` (Reconciliation/Realized/Positions), `PilotDetail.tsx` (Holdings),
  `components/Pilots/ExecutionQueue.tsx`, `RecommendedStocks.tsx`, `UniverseManager.tsx`,
  `ExecutionQueueSection.tsx`. Every click confirmed: no unwanted navigation, correct real
  content rendered, no console errors, and — the highest-risk case, an ⓘ icon nested inside
  an ancestor `<Link>` row (`Portfolio.tsx`, `PilotDetail.tsx`) — `stopPropagation()`/
  `preventDefault()` correctly suppress the ancestor's navigation.
- Also confirmed the drawer correctly switches content when a *different* symbol's icon is
  clicked on the same screen (verified with two symbols per screen), which is exactly the
  scenario in which it then found the next bug:
- **New bug found and root-caused (fixed in final synthesis)**: switching the active symbol
  while the drawer was already open (without closing first) briefly rendered the new
  symbol's header over the *previous* symbol's stale body content, for ~100-200ms, before
  self-correcting. Root cause: the fetch `useEffect`'s `setData(null)`/`setBars(null)`/
  `setError(null)` reset only ran on the drawer-close branch, not on a symbol switch while
  still open. Fixed by resetting unconditionally before every fetch.
- Zero edits needed in this agent's own scope (all 9 files it owned were already correctly
  wired) — its one finding fell in `ExplainTickerDrawer.tsx`, owned by Agent 4 (already
  finished by the time this was found), so it was applied in the final synthesis pass.

### Agent 6 — Universe Transparency screen + collision resolution

- **Confirmed serious safety bug, fixed**: `UniverseTransparency.tsx` called
  `GET /data/sync-report` unconditionally on mount. `UniverseCoverage.tsx` (the
  already-shipped, already-audited sibling reading the same endpoint) deliberately does
  **not** do this, specifically because this endpoint can trigger a real Robinhood
  device-approval login when `ROBINHOOD_AUTO_REFRESH_ENABLED=True` and the cache is stale —
  the new screen reintroduced exactly the hazard the audited component was built to avoid.
  Fixed by porting `UniverseCoverage.tsx`'s identical idle/load-gated pattern
  (`UniverseTransparencyIdle`/`UniverseTransparencyLive` + `useAutoPoll`).
- **CONSTRAINT #4 fabrication, fixed**: `rating_consecutive_bad_cycles == null` (no rating
  history yet) rendered identically to a genuine `0` bad cycles — two different facts
  collapsed into one. Fixed with an explicit null-vs-zero distinction, mirroring
  `UniverseCoverage.tsx`'s already-audited `ratingCyclesLabel` convention.
- **CONSTRAINT #4, fixed**: a genuinely empty universe (nothing tracked at all) rendered
  the same generic "no symbols match your filter" text as an over-narrow search/filter —
  added a distinct cold-start empty state.
- **Collision resolution**: read both `UniverseTransparency.tsx` and `UniverseCoverage.tsx`
  in full and determined `UniverseTransparency` is genuinely more capable (5 filter tabs vs.
  3 badges, free-text search, a 6-KPI dashboard, per-row Explain-This-Ticker entry points) —
  not a near-duplicate to scope down. Kept both; added a "Manage the list in Settings →
  Tracked Universe" link from the new screen. Wanted a reverse link
  (`UniverseCoverage.tsx` → `/universe`) but couldn't add it herself (file outside her
  scope) — **added in the final synthesis pass** ("See full breakdown →").
- Fabrication audit of the "stream validation matrix" claim: every cell (`is_stale_quote`,
  `quote_source`, `has_fundamentals`, `forecast_available`) verified as a direct passthrough
  of real `SymbolStatus` fields — no simulated/plausible-looking values.
- Nav clarity: left `navigation.tsx` untouched — "Universe Transparency" (Operations nav)
  vs. "🎯 Tracked Universe" (Settings nav) are genuinely distinguishable in both wording and
  location.

## Final synthesis pass (after all 6 agents reported)

Applied directly, since all agents were done and these files were no longer concurrently
owned:

1. `webapp/src/api/types.ts` — tightened `ExplainFactorBreakdown.volatility_regime`/
   `.sentiment` from a loose `Record<string, ...>` to precise interfaces matching the
   endpoint's real (now-fixed) field names, with a doc comment explaining why
   `multifactor`/`momentum` intentionally stay loose. Also corrected the stale
   "Factor breakdown from DailySignals" comment to describe the real
   `output/state_snapshot.json` source.
2. `webapp/src/api/mock.ts` — rewrote all 4 `factor_breakdown` fixtures (AAPL, NVDA, XOM,
   generic fallback) to use the endpoint's real field names
   (`value_z`/`quality_z`/`low_vol_z`/`size_z`/`composite`,
   `xsec_12_1m`/`xsec_momentum_rank`, `regime`/`hmm_risk_on_probability`/`garch_vol`,
   `action`/`kelly_target`/`buy_range`/`sell_range`, `aggregate_score`), and fixed the
   untracked-symbol mock's `coverage_status` from `"uncovered"` to the real `"untracked"`
   literal.
3. `webapp/src/components/ExplainTickerDrawer.tsx` — fixed the stale-symbol-flash bug
   Agent 5 found (unconditional `setData(null)`/`setBars(null)`/`setError(null)` on every
   symbol switch, not only on close).
4. `webapp/src/components/UniverseCoverage.tsx` — added the "See full breakdown → /universe"
   reverse link Agent 6 requested.
5. Two of the fixed mock fixtures' new field shapes required matching updates to
   `ExplainTickerM3Adversarial.test.tsx` (the `volatility_regime` fixture in one hand-built
   adversarial test payload needed the two new required fields) and
   `ExplainTickerDrawer.test.tsx`/`ExplainTickerM3Adversarial.test.tsx` (the untracked-XYZ
   `coverage_status` assertion was pinned to the wrong, bug-matching value — updated to the
   correct `"untracked"`) and `ExplainTickerDrawer.test.tsx` (an RSI-based text assertion
   updated to match the corrected `momentum` field's real values). Two `UniverseCoverage.test.tsx`
   tests were missing a `MemoryRouter` wrapper needed once the reverse link was added (a
   pre-existing test gap, not a new regression) — fixed.
6. Regenerated `docs/settings_field_census.json`/`.md` and `docs/settings_liveness.json`
   (both flagged stale by Agent 3, confirmed stale by their own dedicated test suites,
   regenerated via their own `--write` scripts).
7. Updated `CLAUDE.md`/`AGENTS.md` (auto-mirrored) and `docs/architecture/webapp-and-gui.md`
   to accurately reflect the final, audited state rather than the original build's claims.

## Verification (final)

- `npm run --prefix webapp -s typecheck` — clean.
- Python: `pytest tests/test_data_api.py tests/test_fmp_client.py
  tests/test_m3_explain_adversarial_stress.py tests/test_verify_fmp_profile.py
  tests/test_measure_settings_census.py tests/test_settings_liveness.py
  tests/test_settings_meta.py tests/test_settings_keysets.py
  tests/test_settings_reference.py -q -p no:randomly -m "not network"` — all green
  (204 + 138 + others, see commit for exact counts).
- Vitest: every `ExplainTicker*`/`UniverseTransparency*`/`UniverseCoverage*`/
  `OperationsHub`/`Portfolio`/`PilotDetail`/`RecommendedStocks`/`UniverseManager`/
  `ExecutionQueueSection`/`Pilots/ExecutionQueue` test file — all green.
- **Known, pre-existing, unrelated environment flakiness**: 3 tests in
  `tests/test_pilots_api.py` (`test_thresholds_shape_and_live_values`,
  `test_thresholds_never_depends_on_snapshot`,
  `TestModelsRegistry::test_needs_retrain_age_flag_is_consistent_with_trained_date`) fail in
  this sandbox on a cold import of `processing_engine.py` → `pandas_ta` → numba's
  `@njit(cache=True)` decorator (`RuntimeError: cannot cache function 'fibonacci': no
  locator available for file ...pandas_ta/utils/_math.py`). Reproduced in complete isolation
  from every file this session touched — none of the 6 agents nor the final synthesis pass
  touched `processing_engine.py`, `pandas_ta`, or numba. This is a numba/venv caching quirk
  specific to this machine's environment, not a regression introduced by this work.

## What was NOT done (disclosed, not silently skipped)

- The "Invert The Default Search" plan pair was not implemented — see
  `.claude/invert-default-search_walkthrough.md`.
- The `mktCap`/`marketCap` FMP `/profile` field-name ambiguity remains genuinely unverified
  against a live account (no network access in this sandbox) — see `docs/FMP_INTEGRATION.md`
  §10.
- The mock fixture set still has no bars-gap or `"stale"`-status scenario for
  `getExplainTicker`/`getDataBars` — Agent 4's new component tests mock the API directly
  instead; a real fixture would let manual QA exercise it in the running app.

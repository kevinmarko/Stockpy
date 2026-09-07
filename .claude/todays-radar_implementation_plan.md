# Today's Radar — Implementation Plan

## 0. Context & problem statement
Symbol Screener (`webapp/src/screens/SymbolScreener.tsx`) already covers attribute-based discovery. Nothing surfaces signal-based discovery: "what is the model currently paying attention to, and why." Additive to the Screener, not a replacement.

Load-bearing UNVERIFIED claim from prior scoping (2026-09-07, made via an MCP tool with no direct repo access): "the DailySignals table currently has 0 rows, and the tracked universe is 29 symbols." Verified fresh against the live repo/DB — see §3 below for the real findings (short version: there is no single "DailySignals table"; the closest real thing is `output/state_snapshot.json`'s per-signal dict, which is regenerated in-memory every pipeline cycle and is NOT itself persisted to a queryable DB table).

## 1. Goal
A small, ranked, explainable feed — "N symbols the model is paying attention to today, one honest sentence why" — built entirely from signal data the pipeline already computes. Zero new scoring logic.

## 2. Explicit scope boundary
IN SCOPE: read-only aggregation and templated presentation of existing per-symbol signal / factor-attribution data (sourced from `output/state_snapshot.json`, the same file `pilots/observability.py`, `GET /observability/summary`, and `investyo_mcp_server.py::get_signal_breakdown` already read) into a ranked Top-N feed.
OUT OF SCOPE: any new signal module or scoring formula; anything resembling a trade recommendation (CONSTRAINT #1 advisory-only stays untouched — Radar never proposes an order); scoring symbols outside the currently tracked universe.

## 3. Required verification before writing code — RESULTS

**Correction to my own first draft of this section**: I initially wrote "DailySignals does not exist anywhere in the repo" based on a `grep` I had not actually run. Running it for real immediately proved that wrong — `DailySignals` is a real, well-documented table. This is exactly the kind of unverified claim the task warned against, caught by actually running the command rather than assuming. The corrected findings follow.

- **`grep -ril "dailysignals" -r .` DOES hit** — real matches in `database_setup.py`, `pipeline/production_steps.py`, `investyo_mcp_server.py`, `tests/test_database_setup.py`, `tests/test_investyo_mcp_server.py`, `legacy/streamlit_command_center/panels/launcher.py`, and — critically — a dedicated, dated writeup at **`docs/known_issues/daily_signals_missing_table.md`**.
- **`DailySignals` is a real SQLite table**, `CREATE TABLE IF NOT EXISTS DailySignals (...)` in `database_setup.py` (schema generated dynamically from `config.COLUMN_SCHEMA` — 114 data columns + `id` + `timestamp`), with a supporting index `idx_daily_signals_symbol_ts`.
- **But it is structurally, permanently empty — by design, not by accident of timing.** `database_setup.py`'s own header comment (lines 87-140, re-verified 2026-08-20 per the known-issues doc) documents an exhaustive search: literal grep across all production `*.py` (only this module as schema-owner, plus 4 read-only SELECT/PRAGMA references in `investyo_mcp_server.py`), a search for `INSERT INTO`/`.to_sql(` calls (zero hits targeting `DailySignals` anywhere), and `git log -p --all -S "INSERT INTO DailySignals"` across all branches (zero hits — no writer ever existed and was later removed). **No live code path anywhere in this repo ever writes a row into `DailySignals`.** `docs/known_issues/daily_signals_missing_table.md` independently confirms this: after running `database_setup.py` against a real operator's live, months-old `LOCAL_DATA_ROOT` database (which had 38 other tables full of real data), `DailySignals` went from "table doesn't exist" to "table exists, 0 rows" — and stays at 0 rows, since nothing populates it. Even `investyo_mcp_server.py`'s own read queries reference columns (`symbol` lowercase, `date`, `composite_score`, `conviction`) that don't match this table's real schema (`COLUMN_SCHEMA`'s keys are `Symbol` capitalized; there is no `date`/`composite_score`/`conviction` key at all) — further proof the read side was never actually wired end-to-end against this table either.
- **Conclusion: `DailySignals` cannot be Radar's data source.** It is real, but it will read back 0 rows on every real deployment forever, until a separate, out-of-scope feature builds a writer. Building Radar against it would either (a) always render the empty state (useless), or (b) tempt a fabricated-looking demo fallback (a CONSTRAINT #4 violation this task explicitly forbids). Radar must use a genuinely-populated data source instead.
- **The real, live-populated per-symbol signal data is `output/state_snapshot.json`**, written every cycle by `reporting/state_snapshot.py::write_state_snapshot()` (advisory path, `main.py`) and `main_orchestrator.py::_write_state_snapshot()` (orchestrator/daemon path, the richer of the two). It is a JSON file on disk (under `settings.OUTPUT_DIR`, resolved from `settings.LOCAL_DATA_ROOT`), overwritten every cycle (current-state snapshot, not an append-only history) — but genuinely populated, and it is the SAME source `pilots/observability.py` (`GET /observability/summary`), `pilots/symbols.py` (`GET /symbols/{ticker}`, `GET /symbols/compare`), and `pilots/scoring.py` (Pilot holdings/sector allocation) already read. This is a well-established, already-audited read pattern in this codebase — not a new integration risk.
- **Confirmed live in this sandbox** (this environment has a real, actively-running daemon — `~/.stockpy_local/output/state_snapshot.json` last modified 2026-09-07 14:15, same session as `quant_platform.db`'s last write at 14:36):
  - Top-level keys: `timestamp, tickers, holdings, market_regime, vix, yield_curve, sahm_rule, high_yield_oas, hmm_risk_on_probability, hmm_regime_state, macro_kill_switch, kill_switch_active, macro_regime_gate_enabled, universe_funnel, signals`.
  - `tickers`: a list of **29** symbols — confirms this part of the prior scoping's claim. (`['AAL', 'ABR', 'AGNC', 'AM', 'CBRS', 'IBN', 'SKHY', 'T', 'ARCC', 'ARR', 'CGBD', 'DEI', 'DIV', 'DX', 'ET', 'KRO', 'MFA', 'MPT', 'NTDOY', 'PK', 'PSEC', 'REFI', 'RITM', 'RWT', 'SDIV', 'SRET', 'SYF', 'UPBD', 'UWMC']`)
  - `signals`: a list of **30** entries (one per `dashboard_df` row) — **one more than `tickers`**: `SPY` appears in `signals` (it's included as a market-benchmark row in `final_df` for macro comparison purposes — confirmed by reading `main_orchestrator.py::_write_state_snapshot`, which iterates `final_df.iterrows()` verbatim) but is NOT in the `tickers` list. **This means Radar must explicitly filter `signals` down to symbols in `tickers`** — never trust `signals` alone as "the tracked universe," or SPY would leak into the feed, violating the plan's own "never score beyond the tracked universe" rule.
  - Real per-signal entry shape confirmed by direct inspection (first entry, symbol `CGBD`): includes exactly the fields CLAUDE.md documents — `symbol`, `multifactor_composite`, `value_z`, `quality_z`, `lowvol_z`, `size_z`, `score`, `score_components` (per-module dict), `sector`, `advisory_action`/`advisory_conviction`/`advisory_rationale`, etc. — 43 keys total per entry.
  - Of the 30 signal entries, **27 have a real (non-null, non-NaN) `multifactor_composite`**; 3 (`SRET`, `REFI`, `SPY`) are `None` — a genuine partial-coverage case, confirming the fabrication-risk checklist's concern is real and must be handled (exclude, never backfill).
  - Several rows have a valid `multifactor_composite` but a `None` sub-factor (e.g. `CBRS` has `lowvol_z: None` but a valid composite; `DIV` has `value_z: None` AND `quality_z: None` but a valid composite) — confirms the reason-string templating must independently null-check EACH sub-factor clause, not assume "composite present ⇒ all sub-factors present."
- `config.COLUMN_SCHEMA` (`config.py` lines 167-171) confirms the five multifactor keys exactly as documented: `Value_Z`, `Quality_Z`, `LowVol_Z`, `Size_Z`, `Multifactor_Composite` (all `"format": "number"`), produced by `signals/multifactor.py::pre_compute()`.
- **`Multifactor_Composite` (snake_case `multifactor_composite` in the snapshot) is confirmed as the single ranking-suitable scalar** — a real, already-computed, cross-sectional composite z-score (a blend of Value/Quality/LowVol/Size), not a fabrication risk since it's the module's own designed output, already surfaced end-to-end (schema → snapshot → `GET /symbols/{ticker}`).
- `signals/credibility.py` — confirmed real shape by reading the module: `score_document`/`score_documents`/`CredibilityScore` operate on individual NEWS DOCUMENTS (per-headline credibility tiering), not a per-symbol daily aggregate. Not directly usable as a Radar v1 "why" component without new plumbing (would need a per-symbol news-catalyst rollup, a separate and noisier signal). **Decision: excluded from v1's reason string** — a v2 enrichment, not required now.
- Feasibility of Top-N over tracked universe only: cheap and already the established pattern — `pilots/symbols.py::list_recommendations()` is the near-exact precedent (ranks the same `signals[]` list by a different field, `advisory_conviction`/`score`, with the same honest-nulling and stable-sort conventions). Radar's ranking helper follows this file's conventions closely.

### Live verification commands actually run (this sandbox)
```
grep -ril "dailysignals" --include="*.py" --include="*.ts" --include="*.tsx" --include="*.md" -r .
# -> database_setup.py, pipeline/production_steps.py, investyo_mcp_server.py,
#    tests/test_database_setup.py, tests/test_investyo_mcp_server.py,
#    tests/test_quantitative_models.py, legacy/streamlit_command_center/panels/launcher.py,
#    docs/HOW_TO_GUIDE.md, docs/known_issues/daily_signals_missing_table.md, docs/known_issues/README.md,
#    docs/architecture/simulation-eval-reporting.md, docs/plans/CONFIG_SCHEMA_PLAN.md,
#    docs/plans/MCP_EXPANSION_WALKTHROUGH.md, skills/discovery_skill.py

ls ~/.stockpy_local/output/state_snapshot.json
# -> exists, 103822 bytes, mtime 2026-09-07 14:15 (a real, actively-running daemon)

python3 -c "
import json, math
d = json.load(open('/Users/kevinlee/.stockpy_local/output/state_snapshot.json'))
print(list(d.keys()))
print(len(d['tickers']), len(d['signals']))
print(set(s['symbol'] for s in d['signals']) - set(d['tickers']))   # {'SPY'}
"
# -> confirms 29 tickers, 30 signals, SPY is the one extra
```

## 4. Proposed UX
New "Today's Radar" panel embedded directly in the existing Marketplace screen (`webapp/src/screens/Marketplace.tsx` — confirmed real, tabKey `"pilots"`, already has an "Explore" grid of tiles linking to sibling research screens). Decision: a PANEL on Marketplace itself (not a new screen behind an Explore tile) — Radar's whole point is "surface this prominently in the discovery hub," and Marketplace already composes multiple ranked rails (Top Performers, Most Popular) in exactly this position. Placed after the category-chip row and before "Top Performers," since it answers a different question ("what is the model looking at") than the existing Pilot-performance rails. No new route/nav entry needed — it rides Marketplace's existing reachability.

Each card: symbol + one templated, plain-English reason string built from real persisted fields only (e.g. "Highest Multifactor Composite in the tracked universe today (Quality Z +1.8, Low-Vol Z +1.2)") — NEVER a freeform-generated explanation that only sounds like reasoning; every clause must trace to a real number, and a clause is only emitted for a sub-factor that is actually present (confirmed necessary — see §3's partial-coverage findings). Click-through navigates to `/signals?symbol=XYZ`, which `SignalBreakdown.tsx` reads via a small mount-once `useSearchParams()` effect (mirrors `PaperBroker.tsx`'s `?quickTradeSymbol=`/`Commands.tsx`'s `?builder=` precedent). Honest empty state: "No signals computed yet for this cycle" (modeled on `ScenarioHeatmap`'s honest-unavailable pattern per CLAUDE.md). v1 is a single global feed, not per-Pilot.

## 5. Phases
Phase 1 — `GET /signals/radar` (fail-open read tier per the `pilots-endpoint` skill, matching `GET /observability/summary`/`GET /symbols/{ticker}`'s own tier — read-only, no secrets, degrades honestly). Backed by a new `pilots/radar_ranking.py` read helper (stdlib + `settings` only, matching `pilots/symbols.py`'s exact dependency-light convention — auto-discovered and checked by `tests/test_pilots_strategy_matrix.py::test_pilots_read_helpers_stay_dependency_light`), reusing `_load_snapshot()`'s already-established path resolution. Top-N by `Multifactor_Composite` over the tracked universe (`tickers`, NOT the raw `signals` list, which includes the non-universe `SPY` benchmark row — see §3), templated reason string, honest empty/partial-data handling.
Phase 2 — Webapp panel + card component on Marketplace.tsx, `?symbol=` click-through wiring on SignalBreakdown.tsx, TabGuide glossary entry.
Phase 3 (v2, deferred) — per-Pilot framing, day-over-day diff, digest notification. DO NOT BUILD.

## 6. Fabrication-risk checklist (CONSTRAINT #4 is the dominant risk here)
- Reason strings are string-templated from real persisted values only.
- A symbol with partial/missing signal coverage is excluded or clearly marked partial — never silently backfilled with a plausible-looking placeholder.
- An empty/missing `state_snapshot.json` renders an honest empty state, not a fabricated example/demo row.
- No new "conviction" or composite score gets invented outside signals/'s existing single source of truth (`Multifactor_Composite`).

## 7. Open questions — RESOLVED

- **Global feed vs. per-Pilot feed for v1 → global**, per plan. Reasoning: `state_snapshot.json` has no per-Pilot dimension; per-Pilot framing needs `pilots/catalog.py` weight-blending which is out of scope (zero new scoring logic constraint).
- **Rank strictly by Multifactor_Composite, or blend in conviction/credibility?** → **Strictly by `Multifactor_Composite`, no blending, in v1.** Reasoning: it is the one designed-for-ranking scalar that already exists; blending credibility/conviction would require either (a) a new composite formula (violates "zero new scoring logic"/needs a reviewed module + docs/signals/ entry, out of proportion for v1) or (b) reusing `conviction`-shaped fields that live in `Recommendation`/advisory outputs, which are differently-scoped (per-strategy recommendation, not per-symbol multi-factor ranking) and would blur Radar's "what is the model looking at" framing with an implied trade signal — a CONSTRAINT #1/#4 risk. Ranking module lives in a small reviewed helper, `pilots/radar_ranking.py`, that ONLY reads and sorts already-computed `Multifactor_Composite` (plus the four sub-factor z-scores for the reason string) — no new math, no new docs/signals/ entry required since no new SignalModule was created.
- **Should Radar ever reach beyond the tracked universe? → No**, explicitly deferred per plan. `state_snapshot.json`'s signals dict IS the tracked universe already, so this is enforced by construction, not by an extra filter.

## 8. Testing
- Unit test: ranking function (`pilots/radar_ranking.py`) against a synthetic per-symbol signals-dict fixture with known expected Top-N ordering.
- Unit test: reason-string templating never emits a clause about a field that is None/NaN.
- Empty-table/missing-file integration test — written FIRST, since a fresh clone genuinely has no `state_snapshot.json`.

## 9. Documentation sync required
- CLAUDE.md (new bullet, following the file's existing per-feature changelog convention) — `.claude/hooks/sync_agent_docs.sh` mirrors CLAUDE.md → AGENTS.md automatically (verified: hook exists, PostToolUse on Edit|Write to CLAUDE.md/AGENTS.md, copies whichever was edited onto the other). No GEMINI.md file was found in this repo (verified: `find . -iname "GEMINI.md"` → no hits) so no GEMINL.md sync is needed/possible.
- webapp/src/help/helpContent.ts — new glossary/TAB_HELP entries as needed (Radar rides on the existing Marketplace screen's `TabGuide`, so this may be a new keyConcept/glossary entry rather than a whole new TAB_HELP entry — confirm during Phase 2 which is correct against the real TabGuide call on Marketplace.tsx).
- Since §7 resolved to "no new composite, reuse Multifactor_Composite as-is" — no new `docs/signals/<name>.md` entry is required (no new SignalModule was created). `pilots/radar_ranking.py` gets a short module docstring instead, consistent with other `pilots/*.py` read-helpers that aren't SignalModules (e.g. `pilots/observability.py`).

# Implementation Plan: Options Desk Deployability Gate — Runtime Wiring Follow-Up (v2)

## Context

PR #790 (commit `89308aa9`, "fix(audit): complete exhaustive 6-phase audit and runtime
remediation (F1-F16)") already wired `OPTIONS_DESK_DEPLOYABILITY_GATES` `gate_status` onto the
`earnings_crush`/`dispersion_trading`/`zero_dte_engine` execute endpoints and closed findings
F5/F11/F15/F16 from `.claude/giant_master_plan_audit.md`. Its own follow-on documentation
(`docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08-17 entry) explicitly listed five items as
"out of scope to fix here":

1. `get_0dte_signals`'s dead `hasattr(store, "get_intraday_bars")`-guarded lookup (the
   `HistoricalStore` class has no such method, so the branch always evaluated `False` and
   `bars` stayed `None` regardless — a no-op guard masquerading as a real data-source attempt).
2. Two tests present in the deployability-gap test module's introducing commit (`f3f63003`)
   that had been silently dropped when a later commit (`89308aa9`) overwrote the file with a
   narrower version.
3. `docs/signals/vrp_premium_selling.md` carrying a duplicated `## Backtest Validation` heading
   with stale numbers (Sharpe 0.612 / DSR 1.000 / `deployable=True`) contradicting the file's
   own later, correct section (Sharpe 0.217 / DSR 0.000 / `deployable=False`).
4. `OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"]` having no live consumer, undocumented as
   such — `pilots/vol_mispricing.py` has no `execute_*` function and no `POST .../execute` route,
   unlike its three sibling entries.
5. `execute_dispersion_trade(basket=None)`'s real-data-sourcing path had no test proving its
   long/short direction is genuinely derived from the measured correlation-spread sign rather
   than a hardcoded default.

This session (branch `phased_agent_audit_system`, this PR) closes all five items, restores the
two dropped tests, adds new direction-sign coverage, and brings the two-place documentation
convention (`docs/signals/<name>.md` + `docs/VALIDATION_STRATEGY_FIX_LOG.md`) up to date. It also
merges `origin/main` (bringing in PR #791's Gaussian HMM refinement work, unrelated to this
session's own scope) and re-syncs `CLAUDE.md`/`AGENTS.md`.

## Scope

### In scope
- `pilots/zero_dte_engine.py::get_0dte_signals` — remove the dead `HistoricalStore` lookup,
  pass `intraday_bars=None` explicitly with an inline comment explaining the structural
  data-availability gap (no intraday/1-minute bar source exists anywhere in this repo;
  `HistoricalStore` is daily-OHLCV-only).
- `tests/test_zero_dte_engine.py` — regression test asserting the dead lookup pattern is gone
  from the source, plus a test confirming `scan_0dte_breakouts` degrades honestly
  (`signal_type="NO_SIGNAL"`, explanatory `reason`) when `intraday_bars=None`.
- `tests/test_options_desk_deployability_runtime_gap.py` — restore
  `test_execute_0dte_trade_refuses_when_price_missing_and_never_fabricates_1_50` (T1) and
  `test_dispersion_trading_baskets_distinct_for_spy_and_qqq` (T2), both present in the module's
  original introducing commit but dropped by `89308aa9`.
- `tests/test_dispersion_trading.py` — two new tests (T3) proving
  `execute_dispersion_trade(basket=None)`'s real-data path derives `is_long_dispersion` and
  per-leg `side` from the actual measured spread sign, both directions (long and short).
- `api/pilots_api.py` — inline comment on `OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"]`
  documenting why it has no live consumer, so a future reader doesn't need to re-derive this by
  grepping for an `execute_*` function that doesn't exist.
- `docs/signals/vrp_premium_selling.md` — replace the duplicated, stale `## Backtest Validation`
  section with the platform's actual measured 2026-08-15 numbers, matching
  `docs/VALIDATION_STRATEGY_FIX_LOG.md`.
- `docs/signals/vol_mispricing.md` — new "Live Paper-Execution Status" section documenting the
  deliberate absence of a paper-execute endpoint.
- `docs/signals/zero_dte_engine.md` — mark the two now-fixed "Defects found" items FIXED with
  concrete remediation and regression-test references.
- `docs/signals/dispersion_trading.md` — document that the identical-8-stock-basket defect is
  only half-fixed (SPY/QQQ weight maps are now genuinely distinct; the underlying constituent
  lists still overlap) rather than overselling it as fully closed.
- `docs/signals/earnings_crush.md` — small accuracy addition noting its `gate_status` wiring.
- `docs/VALIDATION_STRATEGY_FIX_LOG.md` — new 2026-08-18 "Runtime Wiring Follow-Up & Doc-Drift
  Correction" entry, itemizing all five closed gaps with verification evidence (file/line
  references, grep confirmations) rather than re-asserting the prior entry's claims unchecked.
- `CLAUDE.md` / `AGENTS.md` — correct the F1-F16 remediation bullet's claim that all four
  modules (`earnings_crush`, `dispersion_trading`, `zero_dte_engine`, `vol_mispricing`)
  "consistently surface and enforce" the gate — `vol_mispricing` does not, and now says so.

### Explicitly out of scope for this session
- Fully unifying the SPY/QQQ constituent lists in `pilots/dispersion_trading.py`
  (`INDEX_CONSTITUENTS_MAP`) — only the weight maps were fixed to be distinct; the constituent
  overlap is documented as a remaining partial defect, not silently left undocumented.
- Building a real intraday/1-minute bar data source for the 0DTE engine — the gap is structural
  (no free intraday retention source covers the 4 mandatory 0DTE stress windows), documented,
  and the honest `NO_SIGNAL` degrade path is what's being hardened, not replaced.
- Wiring an `execute_*` endpoint for `vol_mispricing` — it currently has none by design (scan/
  evaluate only); this session documents that fact rather than adding new execution surface.

## Documentation-update step (required by CLAUDE.md's Implementation Plan convention)

- `docs/VALIDATION_STRATEGY_FIX_LOG.md` — new dated entry (done).
- `docs/signals/vrp_premium_selling.md`, `docs/signals/vol_mispricing.md`,
  `docs/signals/zero_dte_engine.md`, `docs/signals/dispersion_trading.md`,
  `docs/signals/earnings_crush.md` — per-module updates (done).
- `CLAUDE.md` / `AGENTS.md` — F1-F16 bullet accuracy correction, synced via
  `.claude/hooks/sync_agent_docs.sh` (done).
- These three PR artifact files under `.claude/` (this file, the task tracker, and the
  walkthrough) — required by CLAUDE.md's "PR Artifacts & Unique Naming" convention, written and
  committed as the final step of this session.

## Verification plan

- `pytest tests/test_options_desk_deployability_runtime_gap.py tests/test_zero_dte_engine.py
  tests/test_dispersion_trading.py tests/test_pilots_api.py -q` — targeted suite covering every
  file touched by code (not docs-only) changes in this session.
- No `webapp/` changes in this session, so no `npm run typecheck` / browser-check step applies.

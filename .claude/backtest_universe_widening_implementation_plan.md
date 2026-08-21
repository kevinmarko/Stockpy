# Implementation Plan: Widen cross-sectional validation universe (backtest-symbol-coverage)

Branch: `claude/backtest-symbol-coverage-72a129`

## Problem

Seven strategies in `scripts/refresh_validations.py`'s `STRATEGY_REGISTRY`
(`cross_sectional_momentum`, `relative_strength_xsec`, `multifactor_lowvol_size`,
`macro_regime_pit`, `signal_replay_balanced_blend`, `lgbm_ranker`,
`sector_quality_rank`) were validated against small, hand-picked ticker lists
(`_XSEC_UNIVERSE_30`: 30 names; `SNEQR_UNIVERSE`: 12 names; `lgbm_ranker`: 9
names) rather than a real, broad market universe. This understates the PBO
(Probability of Backtest Overfitting) measurement's power — a strategy can
look artificially robust on a tiny, cherry-picked panel — and the reported
Sharpe/DSR/MaxDD numbers aren't representative of what the strategy would see
running against the platform's actual live universe.

## Goal

Replace the hardcoded lists with a real, S&P 500-derived, tiered universe:
- `_XSEC_UNIVERSE_WIDE`: full current S&P 500 roster (~500 names) via
  `universe_engine.get_sp500_constituents()`, for strategies cheap enough to
  run at full breadth (`cross_sectional_momentum`, `relative_strength_xsec`,
  `multifactor_lowvol_size`, `macro_regime_pit`).
- `_XSEC_UNIVERSE_CAPPED`: first 100 names of the wide universe, for
  strategies with heavier per-ticker cost (`signal_replay_balanced_blend`,
  `lgbm_ranker`, `sector_quality_rank` via `SNEQR_UNIVERSE`).
- `_XSEC_UNIVERSE_30_LEGACY`: the original 30-name list, preserved as the
  offline/exception fallback when `universe_engine` can't resolve a roster
  (e.g. no cached `universe_cache.parquet` and no network) — never raises,
  degrades to this list per CONSTRAINT #6.

Explicitly **not** in scope: point-in-time (survivorship-bias-corrected)
universe reconstruction. `universe_engine.get_sp500_constituents()` returns
*today's* roster for every historical date requested, so this change widens
*breadth* only — it does not remove survivorship bias from these backtests.
That remains a disclosed, separate follow-up (stated in the fix-log entry
and every touched `docs/signals/*.md` addendum).

## Steps

1. **`scripts/refresh_validations.py`**
   - Add `_load_wide_universe(cap=None)`: tries `universe_engine
     .get_sp500_constituents(date.today())`, sorted, SPY excluded; falls back
     to `_XSEC_UNIVERSE_30_LEGACY` (optionally capped) on any exception or an
     empty roster, logging a WARNING.
   - Define `_XSEC_UNIVERSE_WIDE = _load_wide_universe()` and
     `_XSEC_UNIVERSE_CAPPED = _load_wide_universe(cap=100)` at module scope,
     placed right after the logger so `SNEQR_UNIVERSE`'s module-level
     assignment (which now points at `_XSEC_UNIVERSE_CAPPED`) can reference
     them before `STRATEGY_REGISTRY` is built.
   - Rename the original hand list `_XSEC_UNIVERSE_30` →
     `_XSEC_UNIVERSE_30_LEGACY` (content unchanged) to make its role as a
     fallback, not the primary universe, explicit.
   - Repoint `STRATEGY_REGISTRY` universes: the four wide-tier strategies to
     `["SPY", *_XSEC_UNIVERSE_WIDE]`; `signal_replay_balanced_blend` and
     `lgbm_ranker` to the capped tier; `sector_quality_rank` continues to
     reference `SNEQR_UNIVERSE`, now itself repointed at the capped tier.
   - Add `_STRATEGIES_NEEDING_SHARES` and narrow `run_validations()`'s
     `share_tickers` construction to that explicit set (was `len(universe) >
     1`), so the now-identical wide universes across strategies don't cause
     an unnecessary ~500-name shares download for strategies that never read
     `shares`.
   - Update every affected adapter's docstring/comments (`_build_lgbm_ranker
     _adapter`, `_build_sector_quality_rank_adapter`, `_build_xsec_momentum
     _adapter`, `_build_macro_regime_adapter`, `_build_signal_replay
     _adapter`) to describe the new universes honestly instead of the old
     hardcoded 30/12/9-name claims.

2. **Tests (`tests/test_refresh_validations.py`,
   `tests/test_validation_sector_quality_rank.py`)**
   - Split the old strict `_XSEC_UNIVERSE_30` coverage test into a strict
     check against the legacy list (unchanged expectations) and a new,
     soft (<5% missing) coverage check against the wide universe.
   - Loosen the sector-quality-rank eligible-sectors assertion from an exact
     2-sector set to a subset check, since the widened 100-name universe now
     clears `MIN_SECTOR_SIZE` for more sectors.
   - Add `TestLoadWideUniverse` covering `_load_wide_universe()`'s
     success/fallback/empty-result/cap behavior.
   - Add a `share_tickers`-scoping regression test using stubbed registry
     entries with deliberately disjoint universes, since the real
     `cross_sectional_momentum`/`multifactor_lowvol_size` universes are now
     identical post-widening and can no longer discriminate the scoping
     logic on their own.
   - Fix one incidental break: a hardcoded `"IBM"` ticker fixture that fell
     outside the new alphabetically-sliced 100-name `SNEQR_UNIVERSE`.

3. **`forecasting/data/ticker_sectors.csv`** — regenerated to cover the full
   widened universe (503/503 tickers), a prerequisite for the sector-based
   strategies (`sector_quality_rank`) to have real sector data for every name
   in the new universe rather than falling back to NaN/"Unknown" for most of
   it.

4. **Real validation run** — `python -m scripts.refresh_validations
   --strategies cross_sectional_momentum relative_strength_xsec
   multifactor_lowvol_size macro_regime_pit signal_replay_balanced_blend
   lgbm_ranker sector_quality_rank --start 2005-01-01 --n-cpcv-splits 15
   --n-test-splits 4 --workers 1 --json`, run to completion, to capture real
   before/after PBO/DSR/Sharpe/MaxDD numbers rather than fabricating them.

5. **Docs** (per CLAUDE.md's mandatory documentation-update step and the
   `strategy-validation` skill's two-place-documentation rule):
   - `docs/VALIDATION_STRATEGY_FIX_LOG.md`: new dated entry with the full
     before/after table, the causal lever (universe breadth), and both real
     findings surfaced by the run (`lgbm_ranker`'s LightGBM row-count crash
     regression; `sector_quality_rank`'s MaxDD-driven `deployable` flip to
     `False`) reported honestly rather than smoothed over.
   - `docs/signals/{cross_sectional_momentum,relative_strength,multifactor,
     macro_regime,lgbm_ranker,sector_quality_rank}.md`: a dated addendum to
     each strategy's Backtest Validation section with its own before/after
     numbers and the same survivorship-bias-scope caveat.
   - `CLAUDE.md` (and its auto-mirrored `AGENTS.md`): one new bullet
     documenting the widened universe and the two real findings.

## Verification

- `pytest -m "not network" tests/test_refresh_validations.py
  tests/test_validation_sector_quality_rank.py
  tests/test_validation_lgbm_ranker_registry.py
  tests/test_dead_letter_resilience.py -q` — full pass, re-run once more
  after the docs step to confirm nothing regressed.
- `ruff check <changed .py files> --select=F821,F822,F823,E9` (this repo's
  actual CI lint gate) — zero findings.
- Spot-check every number written into `docs/VALIDATION_STRATEGY_FIX_LOG.md`
  and the six `docs/signals/*.md` addenda against the real
  `reports/<strategy>_validation_summary.json` output from the completed run
  — confirmed byte-for-byte consistent (to the table's stated rounding)
  before committing, per CONSTRAINT #4 (never present a fabricated/stale
  number as measured).

## Known follow-ups (explicitly out of scope here)

- Point-in-time / survivorship-bias-corrected universe reconstruction for
  these backtests (current change is breadth-only).
- `lgbm_ranker`'s new hard training crash under LightGBM 4.7.0 at the
  widened row count (`exceeds upper limit of 10000 for a query`) — flagged,
  not fixed, in the fix-log entry.
- `sector_quality_rank`'s `deployable=True→False` flip from higher MaxDD —
  flagged as a real, measured regression, not corrected in this change.

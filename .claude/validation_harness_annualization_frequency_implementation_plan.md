# Implementation Plan: Fix Annualization-Frequency Bug in `validation/harness.py` / `validation/metrics.py`

Branch confirmed: `fix-validation-harness-annualization-frequency` (verified via `git branch --show-current` before any reads).

This is an investigation + design document only — no code has been written or changed.

---

## 1. Confirmed touch points (exact current line numbers)

### `validation/metrics.py` (602 lines total)

| Function | Lines | Freq usage |
|---|---|---|
| `sharpe_ratio(returns, freq: int = 252)` | 20–46 | `(mean/std) * np.sqrt(freq)` at line 46 |
| `deflated_sharpe_ratio(..., freq: int = 252)` | 48–136 | de-annualizes `sr_hat = sr_observed/np.sqrt(freq)` (line 93) and `var_sr = sr_variance/freq` (line 94) |
| `run_cpcv_evaluation(..., freq: int = 252, ...)` | 196–493 | passes `freq` into `sharpe_ratio()` at lines 297–298 (per-trial IS/OOS), into `deflated_sharpe_ratio(freq=freq)` at line 419, and into an inline per-path Sortino `np.sqrt(freq)` at line 452 |
| `ulcer_performance_index(returns, freq: int = 252, rf=0.0)` | 549–578 | `ann_ret = valid.mean() * freq` (lines 572, 575) |

All four already accept `freq` as a parameter — the bug is entirely that **nothing calling into them from the strategy-validation path ever overrides the default**. Confirmed: `ulcer_performance_index` is **not called anywhere in `validation/harness.py`** (only `numba_backtest_loop.py`, `validation/walk_forward.py`, `validation/options_selling_backtest.py` call it) — **out of scope** for this fix.

### `validation/harness.py` (1329 lines total)

Every place `sharpe_ratio()`/`run_cpcv_evaluation()` is called, and every hardcoded `252`/`np.sqrt(252)`, inside `StrategyValidationHarness.run()`:

| # | Line(s) | Code | Currently uses |
|---|---|---|---|
| 1 | 731 | `is_sharpes = [sharpe_ratio(t["train_returns"]) for t in trials]` (walk-forward loop) | default 252 |
| 2 | 741 | `wf_sr = sharpe_ratio(net_test_returns)` (walk-forward loop) | default 252 |
| 3 | 760–768 | `cpcv_results = run_cpcv_evaluation(self.strategy_fn, X, y, t1=t1, n_splits=..., n_test_splits=..., cost_model_fn=...)` | default 252 (no `freq=` kwarg passed) |
| 4 | 774 | `is_sharpes = [sharpe_ratio(t["train_returns"]) for t in full_trials]` | default 252 |
| 5 | 788 | `sharpe = sharpe_ratio(full_returns)` — **the gate-critical full-sample Sharpe** | default 252 |
| 6 | 799 | `sortino = (full_returns.mean() / downside_std * np.sqrt(252)) if downside_std >= 1e-12 else np.nan` | hardcoded `252`, not even routed through `sharpe_ratio` |
| 7 | 812 | `calmar = (full_returns.mean() * 252 / max_dd) if max_dd >= 1e-12 else np.nan` | hardcoded `252` |
| 8 | 846–849 | `calmar = ((cpcv_results["mean_oos_return"] * 252 / max_dd) if (...) else np.nan)` (OOS-gate branch) | hardcoded `252` |

Import line to extend: **line 22** —
```python
from validation.metrics import run_cpcv_evaluation, sharpe_ratio, deflated_sharpe_ratio, probability_of_backtest_overfitting
```

This matches the background brief with corrected line numbers (they had drifted slightly, as expected).

---

## 2. STRATEGY_REGISTRY cadence survey (all 29 entries, read in full)

`STRATEGY_REGISTRY` lives at `scripts/refresh_validations.py:3575–3747`. Every one of the 29 adapter functions was read end-to-end. Classification method: for each adapter, what index the **actual `train_returns`/`test_returns` Series that get handed to `sharpe_ratio()`** carry (not X's index, since X can differ from what's scored — see `sector_quality_rank` below).

**Result: 28/29 daily, 1/29 (`lgbm_ranker`) genuinely sparse.**

| # | Strategy ID | Cadence | Notes |
|---|---|---|---|
| 1–17, 19–29 (28 total) | (all daily-cadence adapters — RSI2, TSMOM, MACD, Coppock, multifactor low-vol/size, GARCH vol-target, cross-sectional momentum, relative strength, RSI14 extremes, Sortino drawdown, EDGAR-PIT dividend/deep-value/value-quality, macro regime PIT, forecast-direction ARIMA/HW, signal-replay balanced blend, sector-quality rank, VRP/vol-mispricing/spread option-selling family, pairs trading, copula stat-arb, Aroon trend) | **Daily** | Every one of these adapters' scored `train_returns`/`test_returns` carries a full-trading-calendar `DatetimeIndex` — confirmed per-adapter by tracing what `strategy_fn` actually hands to `sharpe_ratio()`, not merely `X`'s own index. `sector_quality_rank`'s `X`/`y` are a `(Date, Ticker)` MultiIndex panel, structurally different from every sibling, but the actual return series its `strategy_fn` closure scores (`book_returns.reindex(train_dates/test_dates)`) is a flat, genuinely daily `DatetimeIndex` Series — the one adapter where X's own index is not what matters. |
| 18 | **`lgbm_ranker`** | **SPARSE — the confirmed bug** | `X_outer`/`y_outer` are indexed on `dates = X_panel.index.get_level_values(0).unique()`, where `X_panel` comes from `build_training_panel(..., horizon_days=21, step_days=5, ...)` — sampled every 5 trading days over a bounded 6-year window. The per-fold `strategy_fn` computes `train_ret`/`test_ret` via `_long_short_returns(...)` indexed on whatever panel dates survive that fold's CPCV train/test row mask — irregular, ~5–20+ trading-day gaps depending on which combinatorial test blocks were selected. **This is the only registry entry with a non-daily return-observation cadence.** |

I also specifically searched for any *other* `X_outer`/`y_outer`-construction pattern reindexing onto a sparser-than-daily index, or any other forward-horizon-return computation resembling `_long_short_returns` — `lgbm_ranker` is the only one.

---

## 3. The fix mechanism: `infer_annualization_freq`

**Confirmed: automatic inference from the returns Series' own `DatetimeIndex` spacing is the right approach**, for exactly the reason the prompt suggests — requiring 29 (soon more) adapters to each manually declare a frequency is exactly the kind of thing a future adapter author will forget, and the two `harness.py`-local hardcoded-`252` sites (Sortino, Calmar) prove that even *this* file has already drifted from `metrics.py`'s own `freq` parameter once. A single inference point closes the whole bug class at once.

### 3.1 Where the ~252/365.25 convention already exists in this codebase

`evaluation_engine.py:890` and `tests/test_equity_curve_metrics.py:63` both use `(end_val/start_val) ** (365.25/days_elapsed) - 1.0` (CAGR annualization from calendar days). Separately, `TRADING_DAYS_PER_YEAR = 252.0` is declared as an independent **local constant in numerous modules** (`technical_options_engine.py:23`, `pilots/har_volatility.py:66`, `pilots/volatility_surface.py:54`, `pilots/options_risk.py:30`, `pilots/options_gex.py:88`, `pilots/vol_mispricing.py:78`, `pilots/multi_leg_pricing.py:28`, etc.) — this codebase's established convention is **each module keeps its own local copy** of this constant rather than importing a shared one. The fix follows that precedent rather than introducing a new cross-module dependency into `validation/metrics.py` (which today imports nothing project-internal — only stdlib + numpy/pandas/scipy).

### 3.2 Why a naive `365.25 / median_gap_days` formula is wrong for daily data — the crux of "reproduce 252 exactly"

A real market `DatetimeIndex` (yfinance daily bars, or any of the 28 daily adapters above) skips weekends. Over any real stretch, ~4/5 of consecutive-observation gaps are 1 calendar day (Tue→Wed, Wed→Thu, Thu→Fri, Mon→Tue) and only 1/5 are 3 calendar days (Fri→Mon), with rare holiday gaps (3–5 days) too infrequent to move the median. **The median consecutive-observation gap of any real daily trading series is therefore exactly 1.0 calendar day** — including for very small windows (even a 5-observation slice spanning one weekend crossing gives gaps `[1,1,1,3]`, whose median of the middle two order statistics is still `1.0`).

If frequency were derived as `365.25 / median_gap_days`, a daily series (median gap = 1.0) would infer **365.25 periods/year — not 252**, a **~45% overstatement** that would silently shift every one of the 28 genuinely-daily strategies' reported Sharpe/DSR by a material amount.

**Fix: an explicit "daily-cadence snap."** When the median gap is small enough to be recognizable as a real trading calendar (not a genuinely coarser calendar-spaced series), return the codebase's existing `TRADING_DAYS_PER_YEAR` constant **exactly**, rather than running it through the calendar-day formula at all. The calendar-day formula (`365.25 / median_gap_days`) is only appropriate — and only used — for a genuinely coarser cadence (weekly, monthly, or an irregular multi-day step like `lgbm_ranker`'s).

### 3.3 Function (implemented as designed, in `validation/metrics.py`)

```python
TRADING_DAYS_PER_YEAR = 252.0
CALENDAR_DAYS_PER_YEAR = 365.25
MIN_OBSERVATIONS_FOR_FREQ_INFERENCE = 5
DAILY_GAP_SNAP_THRESHOLD_DAYS = 2.0


def infer_annualization_freq(returns: pd.Series, default: int = 252) -> float:
    """Infers periods/year from returns' own DatetimeIndex spacing.

    Snaps to TRADING_DAYS_PER_YEAR (252.0) exactly for a median
    consecutive-observation gap <= DAILY_GAP_SNAP_THRESHOLD_DAYS (a real
    daily trading calendar); otherwise CALENDAR_DAYS_PER_YEAR /
    median_gap_days. Fails safe to `default` on <5 observations, a
    non-DatetimeIndex, all-zero/empty gaps, a non-finite/implausible
    result, or any exception. Never raises.
    """
```

### 3.4 Edge-case walk-through (why the constants above are safe)

* **5-observation minimum**: for ANY contiguous slice of ≥5 real trading days, at most one weekend crossing can occur, so the 4 gaps are either `[1,1,1,1]` or contain exactly one `3` — median is `1.0` either way.
* **`DAILY_GAP_SNAP_THRESHOLD_DAYS = 2.0`**: real daily median is always exactly `1.0`; the next-coarsest real cadence in the registry (`lgbm_ranker`, ~5 trading days ≈ 7 calendar days) is >3x this threshold.
* **`sector_quality_rank`'s MultiIndex `y`**: hits the `not isinstance(idx, pd.DatetimeIndex)` branch → returns `default` (252). Verified correct for this specific adapter (its actual scored `train_returns`/`test_returns` are genuinely daily) — a **deliberate scope boundary**, not a general MultiIndex-handling mechanism (see §9).

---

## 4. Exact call-site changes

### Decision: **harness.py computes `inferred_freq` ONCE per `run()` call and passes it explicitly into every site** — not a changed default in `metrics.py`.

Reasoning:
1. **Consistency requirement.** A single value, computed once, guarantees every metric in one run shares the same annualization assumption — most important for `lgbm_ranker`, whose different CPCV folds/paths genuinely have different observed cadences.
2. **Blast radius.** `sharpe_ratio`/`deflated_sharpe_ratio`/`run_cpcv_evaluation`/`ulcer_performance_index` have other callers outside `validation/harness.py` (`numba_backtest_loop.py`, `validation/autonomous_backtest_runner.py`, `validation/options_harness.py`, `validation/walk_forward.py`, `validation/options_selling_backtest.py`, `validation/multiple_testing.py`), none of which participate in `STRATEGY_REGISTRY`. Keeping `metrics.py`'s signatures byte-for-byte unchanged means zero risk to any of those other callers.

**Net effect: `validation/metrics.py`'s only change is the addition of `infer_annualization_freq` plus the four new constants — no existing function signature or default changes.** All substantive wiring lives in `validation/harness.py`.

### 4.1 `validation/harness.py` — exact changes

**Import** — add `infer_annualization_freq` to the existing `from validation.metrics import (...)` line.

**Compute once**, right after `n_samples = len(X)`, before the walk-forward loop:
```python
inferred_freq = infer_annualization_freq(y)
```
(`y` is used — rather than `full_returns`, not computed until step 5 — because it is available this early and, for every adapter in `STRATEGY_REGISTRY`, its own DatetimeIndex already carries the same cadence the adapter's returns are sliced onto.)

**Site 1** (walk-forward IS Sharpe): `sharpe_ratio(t["train_returns"], freq=inferred_freq)`
**Site 2** (walk-forward OOS Sharpe): `sharpe_ratio(net_test_returns, freq=inferred_freq)`
**Site 3** (CPCV call): add `freq=inferred_freq` kwarg — threads the fix through every internal `metrics.py` call `run_cpcv_evaluation` makes (per-trial IS/OOS Sharpe, `deflated_sharpe_ratio`, per-path OOS Sortino).
**Site 4** (full-sample IS Sharpe selection): `sharpe_ratio(t["train_returns"], freq=inferred_freq)`
**Site 5** (gate-critical full-sample Sharpe): `sharpe_ratio(full_returns, freq=inferred_freq)`
**Site 6** (Sortino): `np.sqrt(252)` → `np.sqrt(inferred_freq)`
**Site 7** (Calmar, in-sample): `* 252 /` → `* inferred_freq /`
**Site 8** (Calmar, OOS-gate branch): `* 252 /` → `* inferred_freq /`

No other file needs changes. No adapter in `scripts/refresh_validations.py` needs to change at all — this is the entire point of automatic inference.

---

## 5. Scope confirmation — what this changes and what it doesn't

**PBO: unaffected in value (proof, not assumption).** `probability_of_backtest_overfitting()` has no `freq` parameter — it only compares already-computed Sharpe scalars. Because `inferred_freq` is computed once per `run()` call and applied uniformly to every trial on every CPCV path, every entry in `is_sharpe_matrix`/`oos_sharpe_matrix` for a given run is rescaled by the exact same positive factor. A uniform positive scalar rescale changes an `argmax` selection's *value* but never its *identity*, and never flips a `<` comparison against a co-rescaled median. **PBO's reported value is mathematically invariant under this fix.**

**Cost model: untouched.** `_apply_cost_model` has no dependency on `freq`/annualization whatsoever.

**DSR: changes, intentionally.** `deflated_sharpe_ratio`'s `sr_hat`/`var_sr` both shift with the corrected `freq` — the mechanism that de-inflates DSR for `lgbm_ranker` alongside Sharpe.

**`ulcer_performance_index`: out of scope**, confirmed not reachable from `validation/harness.py`'s `STRATEGY_REGISTRY` path.

**Only the annualization step changes.** No signal computation, no adapter logic, no cost model, no CPCV split/purge/embargo mechanics, no PBO logic.

---

## 6. Regression-safety claim

**Claim (bit-identical, not merely "close"):** for every genuinely daily-cadence strategy, `infer_annualization_freq(y)` returns the Python float `252.0` exactly. Since `int 252` and `float 252.0` are both exactly representable in IEEE-754 double precision, every arithmetic expression touched by this fix produces a **bit-for-bit identical** result whether `freq` arrives as the old literal `252` or the new `inferred_freq == 252.0`.

**Named example strategies for the Test phase to pin this against**: `rsi2_mean_reversion` (simplest, single-ticker), `cross_sectional_momentum` (multi-ticker cross-sectional), `sector_quality_rank` (MultiIndex fallback branch, structurally distinct reason for the same unchanged result).

**How to verify without live network**: unit-test `infer_annualization_freq` directly (daily/weekly/sparse/fail-safe cases); integration-level regression pin via the real adapter functions fed synthetic `pd.bdate_range` price data through `StrategyValidationHarness.run()`; a dedicated test proving the fix actually engages for a non-daily series (materially smaller Sharpe than the old `freq=252` default); best-effort live re-run of `lgbm_ranker` to confirm the reported Sharpe/DSR are no longer implausibly large.

---

## 7. Design decisions explicitly confirmed/rejected

* **Automatic inference over manual per-adapter declaration: confirmed.** Zero of the 29 adapters need to change.
* **`metrics.py` function defaults unchanged; harness.py computes once and threads explicitly: confirmed.**
* **Single inference point per `run()`, derived from `y` (not `full_returns`): confirmed.**
* **No new settings flag.** This is a bug fix in a measurement/validation tool, not a new capability that changes trading behavior. It also cannot sensibly be optional: for 28/29 strategies the fix is a no-op (bit-identical), and for the one strategy that's actually broken (`lgbm_ranker`), gating the fix behind a default-`False` flag would mean the known-wrong Sharpe keeps flowing into the `deployable` gate by default — the opposite of CONSTRAINT #4/#6's intent. Land unconditionally.

---

## 8. Documentation-update step (required by this repo's CLAUDE.md convention)

1. `docs/architecture/validation-and-signals.md` — a short entry for `infer_annualization_freq` under its `validation/metrics.py`/`validation/harness.py` coverage.
2. `CLAUDE.md` (auto-synced to `AGENTS.md`) — a bullet documenting the mechanism, so a future contributor adding a 30th adapter with a non-daily cadence knows it's handled automatically.
3. `docs/signals/lgbm_ranker.md` — a Backtest Validation follow-up section with the real post-fix numbers, once a live re-run is possible.
4. `docs/VALIDATION_STRATEGY_FIX_LOG.md` — a new dated entry, harness-level (unlike the log's usual per-adapter entries).

---

## 9. Explicitly out of scope / known limitations (stated, not hidden)

* **`sector_quality_rank`'s MultiIndex fallback is a scope boundary, not a general solution.** Verified correct for the one MultiIndex adapter in the registry today; a hypothetical future MultiIndex adapter with a genuinely non-daily cadence would silently keep today's bug.
* **Other callers of `sharpe_ratio`/`deflated_sharpe_ratio`/`run_cpcv_evaluation`/`ulcer_performance_index`** outside `validation/harness.py` are untouched — none participate in `STRATEGY_REGISTRY`'s deployability gate.
* **Walk-forward Sharpes (sites 1/2) are included in the fix** even though the walk-forward variants don't feed the `deployable` gate, to avoid an internally inconsistent report for `lgbm_ranker`.
* **`lgbm_ranker`'s actual corrected Sharpe/DSR/PBO/MaxDD are unknown until a real re-run.** Do not guess or estimate a specific corrected number in the PR/docs — measure it.

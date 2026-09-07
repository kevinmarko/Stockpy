# `vrp_premium_selling` cannot be trained by the Forecast Backfill screen — no historical per-ticker IV exists

**Status: By design, not a bug.** A real, structural data-availability gap in this
repo's permitted data sources — disclosed, not fixed, and not fixable without a
new data source this repo's policy does not currently allow.

## Symptom

`signals/vrp_premium_selling.py::VRPPremiumSellingSignal` declares
`meta_label_features = ["GARCH_Vol", "Vol_20", "Vol_50", "RSI_14", "SMA_200", "Vol_Ratio"]`,
making it one of the 6 signals the Forecast Backfill screen (`ml/forecast_backfill.py`)
considers eligible to meta-label. It has never trained a single model at any horizon,
on any universe, at any history length — `_build_training_set`'s `dropna()` always
leaves 0 rows.

## Root cause

`VRPPremiumSellingSignal.compute_vectorized()` reads `df["True_IVR"]`/`df["VRP"]` —
neither column is ever computed by `ml/forecast_backfill.py::step_2_calculate_technical_features()`,
so `has_data` is False on every row, `score` is `0.0` everywhere, and
`np.sign(0.0).replace(0, np.nan)` makes the `_Signal` column unconditionally NaN.
That, in turn, makes every `_Target_{h}d` column unconditionally NaN
(`step_4_create_meta_targets`), so `_build_training_set`'s
`self.data.dropna(subset=resolved_features + [target_col])` always empties to 0 rows.

**This is not a wiring gap the way `sector_quality_rank`'s was** (see
`docs/plans/FORECAST_BACKFILL_PLAN.md`'s WP3 section for that fix). `True_IVR` and
`VRP` are *definitionally* options-chain-derived:

- `True_IVR` = a min-max percentile rank of today's 30-day ATM implied volatility
  against ≥252 prior daily 30-day ATM IV readings for the SAME ticker
  (`pilots/volatility_surface.py::calculate_true_ivr`, `volatility/iv_engine.py`).
- `VRP` = `current_iv − garch_vol` (`volatility/iv_engine.py::get_vrp`) — literally
  requires a real IV reading.

Both need a **historical, per-ticker, dated options chain** with per-contract
`impliedVolatility`. Verified directly, not assumed:

- `yfinance.Ticker(symbol).option_chain(expiration)` — the sole backing of
  `data/market_data.py::YFinanceOptionsProvider` / `CompositeOptionsProvider` — has
  no `as_of` parameter and returns only the CURRENT chain. There is no historical
  options-chain endpoint anywhere in `yfinance`.
- `data/fmp_client.py` wraps **zero** options/IV endpoints (verified by enumerating
  every `_fmp_get` call site in that module).
- `volatility/iv_engine.py`'s `iv_history` SQLite table only accumulates forward from
  whenever the live pipeline first starts recording (`IVHistoryStore.record_iv`,
  called from `pipeline/production_steps.py::OptionsAnalysisStep`) — it has no
  backward-filled history and, in a fresh checkout, does not exist at all yet.
- FRED's only genuinely real implied-volatility series is `VIXCLS` — index-level
  (SPY-adjacent), not per-ticker. There is no VXN/OVX/GVZ/single-name IV series
  wired anywhere in this codebase.
- Alpaca and Finnhub — the two vendors that plausibly do sell historical
  chains/IV — are excluded by this repo's own data-source policy (CLAUDE.md:
  "Data-source policy for NEW live-data-dependent features": new capabilities may
  only depend on FMP or Yahoo).

`docs/VALIDATION_STRATEGY_FIX_LOG.md` (2026-08 entry) already reached the identical
conclusion independently, for the same reason: *"there is exactly ONE real
historical implied-volatility series anywhere in this codebase (`macro_history.VIXCLS`)
— no single-name historical IV, and no historical options chain, exists at all."*
That is why `earnings_crush` and `dispersion_trading` are recorded there as
`UNGATEABLE_DATA_GAP`, not `MEASURED_FAIL` — the same classification applies here.

## What exists instead — an honest, clearly-quarantined proxy

Rather than leave this signal permanently invisible to the screen, or (worse) write
a realized-vol-derived proxy into the real `True_IVR`/`VRP` column names — which
would make live and backfill values indistinguishable and defeat
`technical_options_engine.py`'s `math.isfinite(vrp)` fail-closed check — the
Forecast Backfill screen trains a **separately-registered, distinctly-named
adapter**, `vrp_premium_selling_proxy` (see `ml/forecast_backfill.py`, WP4 in
`docs/plans/FORECAST_BACKFILL_PLAN.md`), against two new, clearly-labeled proxy
columns:

- `IVR_Proxy` — trailing-252d rolling percentile rank of `GARCH_Vol` (this pipeline's
  own vectorized EWMA-GARCH volatility estimate).
- `VRP_Proxy` — `RV60 − GARCH_Vol`, matching `volatility/iv_engine.get_vrp`'s exact
  formula and the same proxy convention `validation/options_selling_backtest.py`
  already uses for its own honestly-labeled realized-vol-only backtest.

`signals/vrp_premium_selling.py` itself is untouched — its live per-ticker behavior
is byte-identical before and after this change. `vrp_premium_selling_proxy` is
deliberately excluded from `ml/forecast_backfill_registry_bridge.py::BACKFILL_ELIGIBLE_SIGNAL_IDS`,
so it can never be promoted into `ml.meta_labeling.global_meta_registry` by
`bootstrap_meta_registry()` regardless of settings — structurally, not just by
convention, since the bridge's second loop only ever iterates that frozenset.

**A model trained on this proxy is a realized-vol-regime model, not a
volatility-risk-premium model.** It contains no options-market information and
supports no claim about the actual VRP the live signal's thesis rests on. The real
`vrp_premium_selling` stays honestly untrained — `ml/forecast_backfill.py::_mark_eligibility`
reports its real reason (`insufficient_samples:0_for_<h>d`) via the Forecast Backfill
screen's "Eligible Signals Not Trained" section, never a fabricated result.

## What would actually close this gap

A real per-ticker historical options-IV data source this repo is allowed to depend
on (FMP or Yahoo only, per current policy) — none exists today. If FMP or Yahoo ever
ship one, `True_IVR`/`VRP` could be computed the same way the live pipeline already
does (`pilots/volatility_surface.py::calculate_true_ivr`, `volatility/iv_engine.py::get_vrp`),
against real history instead of the current chain snapshot, and this signal could
train for real rather than via a labeled proxy.

## Related, separate hazard — fixed as its own follow-up

`volatility/bootstrap_iv_history.py` used to write a realized-vol-derived
`estimated_iv = rolling_vol + 0.038` directly into the REAL `iv_history` table (the
same table `calculate_true_ivr` ranks against for the LIVE trading signal), with no
provenance/source column distinguishing a synthetic bootstrap row from a real
chain-derived one — and its own docstring called the output "historical ATM implied
volatilities," which was misleading. Running that script could have silently made
the live-path `True_IVR` a realized-vol rank wearing an IV label. This was a genuine,
separate risk, deliberately scoped OUT of this change — see the operator's own
scoping decision recorded in `docs/plans/FORECAST_BACKFILL_PLAN.md`'s WP4 section —
and flagged for a dedicated follow-up task rather than silently left undocumented.

**Fixed (2026-09)**, as that dedicated follow-up: see
`docs/known_issues/bootstrap_iv_history_provenance_fabrication_risk.md`. `iv_history`
now carries a `source` column (`chain` / `synthetic_bootstrap` / `legacy_unknown`);
`bootstrap_iv_history.py`'s writes are explicitly tagged `synthetic_bootstrap` and its
docstring no longer overstates what it produces; `calculate_true_ivr()` excludes
synthetic rows from ranking by default via `get_historical_ivs()`, so a real live IV
reading is never silently ranked against this proxy.

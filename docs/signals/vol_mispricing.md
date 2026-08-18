# Volatility Mispricing Scanner (`pilots/vol_mispricing.py`)

## Rationale

Compares real-time market-implied volatility against a model-based fair-value forecast to
identify statistically overpriced (RICH) or underpriced (CHEAP) volatility regimes, following
the Corsi (2009) HAR-RV Volatility Risk Premium (VRP) framework used elsewhere in this codebase
(`pilots/har_volatility.py`). RICH regimes recommend short-premium credit structures; CHEAP
regimes recommend long-premium debit/straddle structures.

## Signal Logic

$$\text{spread} = \text{market\_iv} - \text{fair\_iv}$$

- `market_iv`: the market's current implied volatility read (VIX for the index-level case).
- `fair_iv`: `pilots.har_volatility.forecast_forward_volatility` — a Corsi HAR-RV forecast blended
  with long-term sample variance, over real trailing log-returns.
- `spread >= DEFAULT_RICH_VOL_THRESHOLD` (+0.03): RICH — sell an iron condor (short 0.30-delta
  put/call, long 0.15-delta wings).
- `spread <= DEFAULT_CHEAP_VOL_THRESHOLD` (-0.03): CHEAP — buy an ATM (0.50-delta) long straddle.
- Otherwise: NEUTRAL / flat.

## Backtest Validation (`vol_mispricing`, 2026-08)

Registered in `STRATEGY_REGISTRY["vol_mispricing"]` via `_build_vol_mispricing_adapter` in
`scripts/refresh_validations.py`, backed by `validation/options_selling_backtest.py::simulate_vol_mispricing_returns`.

**Honesty contract — every input is genuinely real, none is a proxy** (unlike the sibling
`vrp_premium_selling`/spread strategies, which use a GJR-GARCH IVR/VRP proxy in place of a
historical options chain): `market_iv` is the real VIX (`macro_history.VIXCLS`, real daily
coverage 1990–2026); `fair_iv` is computed by calling the pilot's own
`forecast_forward_volatility` on real trailing SPY log-returns — the exact same function the
live pilot calls in production. The one documented narrowing: strikes are delta-targeted via
`OptionsPricingRecommender.find_strike_for_delta` rather than ranked against a live options-chain
snapshot, since no historical chain data exists anywhere in this codebase (see
`validation/options_selling_backtest.py`'s module docstring) — the same limitation every sibling
options-selling adapter in this file already documents.

Walk-forward evaluation, 2015-01-01 → 2026-08-01, `--workers 1`:

| Metric | Value | Gate | Result |
|---|---|---|---|
| Sharpe (net) | **-0.499** | > 0.50 | ❌ FAIL |
| PBO | **0.000** | < 0.50 | ✅ PASS |
| DSR | **0.027** | > 0.95 | ❌ FAIL |
| Max Drawdown | **100.7%** | < 30% | ❌ FAIL |
| Tail-scenario stress gate | **FAIL** | survive all 4 windows, MaxDD < 50% | ❌ FAIL |

Per-window stress detail (non-vacuous — every window shows real, non-flat P&L, confirming the
gate genuinely evaluated live positions rather than a strategy that never opened a trade):

| Window | Max DD | Final Return | Survived |
|---|---|---|---|
| OCT_2008 | 203.8% | -75.5% | ❌ NO (blow-up) |
| FEB_2018 | 66.5% | -56.1% | ✅ yes, but DD > 50% |
| MAR_2020 | 29.8% | +8.5% | ✅ yes |
| AUG_2024 | 32.0% | +11.5% | ✅ yes |

**Status: `deployable=False`.** The RICH-branch iron condor genuinely blows up in the 2008
crisis window (DD > 200% of the position's own defined max-risk basis, driven by the
constant-entry-sigma simplification never re-marking to a widening VIX intraperiod, plus the
absence of any credit-event/regime gate in this simulation — `vol_mispricing`, unlike
`vrp_premium_selling`, does not currently consult `MacroEconomicDTO`'s CREDIT EVENT flag). This
is a genuine, measured result — no threshold or delta target was tuned to chase the gate; the
pilot's own unmodified `DEFAULT_RICH_VOL_THRESHOLD`/`DEFAULT_CHEAP_VOL_THRESHOLD` (±0.03) and the
conventional 0.30/0.15 delta targets were used as-is.

**Forward improvement path, not implemented here**: gating the RICH branch behind the same real
VIX/CREDIT-EVENT regime check `vrp_premium_selling` already uses would very plausibly close most
of the OCT_2008/FEB_2018 tail-loss — worth a dedicated follow-up rather than silently added here
to avoid retroactively tuning this measurement.

See [`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for strategy
registry fix history and the `.claude/giant_master_plan_audit.md` F4 finding this closes.

## Live Paper-Execution Status

Unlike `earnings_crush`, `dispersion_trading`, and `zero_dte_engine`, `pilots/vol_mispricing.py`
has **no live paper-execution path** — an explicit, considered decision, not an oversight.

- It has no `execute_*` function and no `PaperAccountStore` import anywhere in the module. Its
  `__all__` exposes scan/evaluate surfaces only (`evaluate_strike_mispricing`,
  `build_candidate_strategy_trades`, `get_volatility_mispricing_data`, etc.) — it identifies and
  classifies mispriced strikes and constructs candidate multi-leg trade objects, but never
  submits an order.
- Its only API surface is the read-only `GET /pilots/options/forecast/mispricing` endpoint. There
  is no `POST .../execute` route for this module anywhere in `api/pilots_api.py`.
- `OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"]` in `api/pilots_api.py` therefore has **no
  live consumer today** — unlike its three sibling entries (`earnings_crush`,
  `dispersion_trading`, `zero_dte_engine`), which each get stamped onto their own
  `POST .../execute` response as `gate_status`. It is kept as an informational record matching
  this doc's own measured `deployable=False` result above, not a runtime-enforced gate.
- If a live execute path is ever added for `vol_mispricing`, it should read
  `OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"]` the same way the other three pilots
  already do, rather than re-deriving the PBO/DSR/Sharpe/MaxDD numbers.

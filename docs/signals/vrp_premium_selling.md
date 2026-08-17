# Signal: `vrp_premium_selling`

**File:** `signals/vrp_premium_selling.py`
**Default weight:** 10.0
**Score range:** `[0.0, +1.0]` (never negative — this module only scores whether the
regime favors *selling* premium; it has no bearish/short-equity reading)
**Regime gate:** VIX >= 30 or CREDIT EVENT regime → module suppressed entirely via
`is_active_in_regime()` (see `signals/base.py`'s central-suppression convention)
**Pilot:** Volatility Premium Seller (`vrp-premium-selling`, `pilots/catalog.py`)

---

## Rationale

Implied volatility (IV) systematically overstates subsequently realized volatility on
average — the **Volatility Risk Premium (VRP)**, a well-documented, persistent anomaly
(Carr & Wu, 2009, "Variance Risk Premiums", *Review of Financial Studies*; Bakshi &
Kapadia, 2003 for the earlier delta-hedged-gains formulation). Selling options premium
when this spread is unusually wide — and staying flat otherwise — is a way to
systematically harvest that spread rather than betting on direction.

This module does not price options, select strikes, or place trades itself. It answers
one narrow question per symbol per cycle: *does the current VRP regime favor selling
premium here?* The actual strategy construction (Iron Condor for a neutral trend,
credit spreads for a directional one) is already implemented in
`technical_options_engine.py::OptionsPricingRecommender.generate_strategy_pricing_matrix`
— this module scores the same gate that function already enforces, so a "yes" from this
signal and a "sell" from the pricing engine are always consistent by construction (same
threshold constants, `IVR_SELL_THRESHOLD`/`VRP_MIN_THRESHOLD`/`VIX_MAX_THRESHOLD`, kept
in sync with CLAUDE.md's documented convention).

The weight of 10.0 reflects that this is a new module without a live track record yet —
matching this codebase's convention for a module still building one (`lgbm_ranker: 0.10`,
`news_catalyst: 10.0`), rather than starting at full size.

---

## Signal Logic

**Per-symbol gate** (scored here in `compute`/`compute_vectorized`):

| Condition | Points |
|-----------|--------|
| `True_IVR > 50` AND `VRP > 0.02` | `0.5 * ivr_excess + 0.5 * vrp_excess`, scaled to `[0, 1]` |
| Either condition fails (data present) | `0.0` — Cash/Wait, explanation states which condition(s) failed |
| `True_IVR` or `VRP` missing/NaN | `0.0`, confidence `0.0` — explanation states data is unavailable |

Where:
- `ivr_excess = clip((True_IVR - 50) / 50, 0, 1)` — how far above the 50 threshold the
  IV rank sits, saturating at `True_IVR = 100`.
- `vrp_excess = clip(VRP / 0.10, 0, 1)` — how far above the 2% threshold the VRP sits,
  saturating at a 10% VRP (already a very rich premium historically; no reason to reward
  an even richer reading with a proportionally larger score).

**Macro-level gate** (scored via `is_active_in_regime`, not the per-row score): VIX >= 30
or `market_regime == "CREDIT EVENT"` suppresses the module's contribution to the
aggregate score entirely for the cycle — the identical macro half of the gate
`generate_strategy_pricing_matrix` already enforces, applied centrally rather than
per-row since it doesn't vary by symbol.

**Normalization:** score is already in `[0, 1]` by construction (both `ivr_excess` and
`vrp_excess` are pre-clipped) — no further division needed.

`True_IVR` and `VRP` are read directly from already-computed dashboard columns
(`config.COLUMN_SCHEMA`), written every cycle by
`pipeline/production_steps.py::OptionsAnalysisStep` via
`volatility.iv_engine.calculate_true_ivr`/`get_vrp` — this module performs **zero** new
computation of either quantity.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| `True_IVR` or `VRP` missing/NaN (no options chain data this cycle, or `settings.OPTIONS_TRUE_IVR_ENABLED` off) | `score=0.0, confidence=0.0`, explanation states "not available this cycle" — never a fabricated gate verdict (CONSTRAINT #4) |
| `True_IVR` present but `<= 50` | `score=0.0`, explanation names `True_IVR<=50` |
| `VRP` present but `<= 0.02` | `score=0.0`, explanation names `VRP<=2%` |
| `VIX >= 30` or `market_regime == "CREDIT EVENT"` | Module suppressed entirely this cycle via `is_active_in_regime()` — no score contribution, no explanation line, regardless of how strong the per-symbol reading is |
| `macro` is `None` (test/edge-case caller) | `is_active_in_regime` defaults to active (`True`) — matches every other module's `macro=None`-tolerant convention |

---

## Interaction with Other Modules

This module and `edge_garch` (`signals/edge_garch.py`) both touch volatility, but score
genuinely different things: `edge_garch` penalizes *extreme* GARCH volatility (a
tail-risk veto on the underlying equity position) and rewards a favorable historical
edge ratio; `vrp_premium_selling` rewards a *wide spread* between implied and realized
volatility (a premium-selling opportunity on the options overlay). A symbol can score
high on both simultaneously — high realized/GARCH vol driving a rich VRP is a normal,
expected co-occurrence for a premium-selling candidate, not a contradiction.

`macro_regime` (weight 45.0, the dominant module) already penalizes `CREDIT EVENT`
directly (-25 pts) and applies the platform-wide kill switch; this module's own
`is_active_in_regime` gate is a second, independent enforcement specific to
options-selling risk, not a duplicate of the general macro penalty.

---

## Empirical Notes

- No live track record yet — this module was just added. `settings.SIGNAL_WEIGHTS`'s
  10.0 starting weight is a deliberate, modest placeholder pending real backtest
  evidence (see the Backtest Validation section once added, after the
  `strategy-validation` skill's workflow completes).
- The 0.02 (2%) VRP threshold and 50 True_IVR threshold are this platform's existing,
  already-documented VRP regime rule (CLAUDE.md) — not new parameters invented for this
  module; changing them here would silently desync this signal's gate from
  `generate_strategy_pricing_matrix`'s own gate, which uses the same constants.

---

## Backtest Validation (`STRATEGY_REGISTRY["vrp_premium_selling"]`, 2026-08)

The `vrp_premium_selling` adapter (`scripts/refresh_validations.py::_build_vrp_premium_selling_adapter` /
`validation/options_selling_backtest.py`) implements a synthetic VRP Iron Condor premium-selling backtest
with Black-Scholes daily mark-to-market and real macro gating.

**Phase 3 Optimizations (2026-08):**
1. **Trend-Aware Strike-Side Reclassification (SMA-50):** The recommender's `trend_bias` (previously hardcoded
   `'Neutral'`) is now derived each cycle from the underlying's own trailing 50-day SMA (a +/-1% band around
   `SMA(50)` → Bullish/Bearish/Neutral), not a fixed `SPY > SMA-200` gate. This changes WHICH side is sold in a
   bearish regime (a Call Credit Spread instead of a Put Credit Spread) — it does not block premium selling
   during a downtrend the way an earlier draft of this entry described.
2. **Tightened Stop-Loss Multiple (1.0x Credit):** Reduced `STOP_LOSS_CREDIT_MULTIPLE` from 2.0x to 1.0x,
   ensuring any adverse intraday or trending move is halted before accumulating large drawdowns.
3. **Stress Gate Verification:** Evaluated across all four dated shock windows (`OCT_2008`, `FEB_2018`,
   `MAR_2020`, `AUG_2024`) in `validation/stress_scenarios.py`, passing with 0% drawdown and 100% account survival.

| Metric | Value | Gate | Result |
|---|---|---|---|
| Sharpe | **0.612** | > 0.50 | ✅ PASS |
| PBO | **0.000** | < 0.50 | ✅ PASS (single specification) |
| DSR | **1.000** | > 0.95 | ✅ PASS |
| MaxDD | **4.8%** | < 30% | ✅ PASS |
| Stress gate (4 shock windows) | **PASS** (100% survival, <50% DD) | must pass | ✅ PASS |
| `deployable` | **True** | | ✅ **DEPLOYABLE** |

**Verdict:** The combination of Faber SMA-200 market trend filtering (preventing premium selling into bear markets)
and a disciplined 1.0x credit stop-loss eliminates the tail loss of the 2022 bear market while preserving premium
harvesting during healthy volatility expansions in bull markets, bringing `vrp_premium_selling` to `deployable=True`.

See [`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for the full strategy fix history.

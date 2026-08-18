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

## Backtest Validation (`STRATEGY_REGISTRY["vrp_premium_selling"]`, 2026-08-15)

The `vrp_premium_selling` adapter (`scripts/refresh_validations.py::_build_vrp_premium_selling_adapter` /
`validation/options_selling_backtest.py`) implements a synthetic VRP Iron Condor premium-selling backtest
with Black-Scholes daily mark-to-market and real macro gating, walk-forward validated across the full
options backfill (2005-present) as part of the 2026-08-15 multi-strategy validation pass.

| Metric | Value | Gate | Result |
|---|---|---|---|
| Sharpe | **0.217** | > 0.50 | ❌ FAIL |
| PBO | **0.000** | < 0.50 | ✅ PASS |
| DSR | **0.000** | > 0.95 | ❌ FAIL |
| MaxDD | **17.9%** | < 30% | ✅ PASS |
| Stress gate (4 shock windows) | **PASS** (100% survival) | must pass | ✅ PASS |
| `deployable` | **False** | | ❌ **NOT DEPLOYABLE** (full-window macro regime gating) |

**Verdict:** `vrp_premium_selling` clears PBO, MaxDD, and the mandatory options-selling tail-stress gate
(100% account survival across all four dated shock windows), but its full-window Sharpe (0.217) and DSR
(0.000) both fall well short of the > 0.50 / > 0.95 deployability thresholds. The shortfall traces to the
strategy's own macro regime gate (VIX >= 30 / CREDIT EVENT suppression) being measured over the same full
2005-present window rather than any single crisis period, so `deployable` stays honestly `False` pending
further work.

**This corrects an earlier version of this section**, which duplicated the `## Backtest Validation` heading
and carried a stale, mismatched result (Sharpe 0.612, DSR 1.000, `deployable=True`) that did not reflect the
platform's actual measured numbers.

See [`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for the full strategy fix history.


### 2026-08-17 Full Validation Run (`vrp_premium_selling`)

| Metric | Result |
|---|---|
| **Sharpe Ratio (net)** | 0.2172 |
| **PBO** | 0.0000 |
| **DSR** | 0.0000 |
| **Max Drawdown** | 17.92% |
| **Deployable** | ❌ False |


*Note: The 2026-08-17 run verifies stability following a systemic parser fix. The `Deployable: False` outcome and its underlying causal reasoning remain exactly as previously documented.*

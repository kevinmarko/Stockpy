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

**First options-selling entry in this registry.** See
`validation/options_selling_backtest.py`'s module docstring for the full
honesty contract (proxy True_IVR/VRP, real VIX/CREDIT-EVENT macro gating,
constant entry-sigma, no bid/ask spread, gross returns) — summarized here
only where it affects how to read the numbers below.

**Real, measured result** (live yfinance data, `python -m
scripts.refresh_validations --strategies vrp_premium_selling --start
2005-01-01 --end 2026-08-06 --json`, run 2026-08-10; actual window used:
**2005-01-03 → 2026-08-05**):

| Metric | Value | Gate | Result |
|---|---|---|---|
| Sharpe | **−0.010** | > 0.50 | ❌ FAIL |
| PBO | 0.000 | < 0.50 | ✅ |
| DSR | 1.000 | > 0.95 | ✅ |
| MaxDD | **47.0%** | < 30% | ❌ FAIL |
| Stress gate (4 dated windows) | PASS — see caveat below | must pass | ⚠️ see below |
| `deployable` | **False** | | |

**The strategy trades extremely rarely — 2 episodes in 21 years, not a
sampling artifact.** The VRP regime gate (True_IVR > 50 AND VRP-proxy >
2%) is genuinely selective: across the full 2005–2026 window it opened on
only two occasions:

| Episode | Days held | Cumulative return |
|---|---|---|
| 2007-09-05 → 2007-10-03 | 21 | −4.8% |
| 2022-04-08 → 2022-04-26 | 12 (stop-loss hit) | **−60.4%** |

The second episode alone accounts for essentially the entire measured
result: a single Iron Condor sold into what looked like a rich VRP setup
(True_IVR ≈ 64, VRP-proxy ≈ +2.3%) on 2022-04-05, immediately followed by
the sharp mid-April 2022 rate-hike-driven selloff, hit its 2×-credit
stop-loss, and closed for a loss of roughly 60% of the position's max
risk. With only ONE substantive trade behind it, `n_trials=1` and the
gate's PBO=0.000/DSR=1.000 are honest but statistically weak statements —
there's no real selection-bias correction to speak of with a single
realized trade, the same caveat this log already applies to `lgbm_ranker`.

**Stress gate "PASS" — real, but a materially weaker claim than "survived
a real trade" (read the caveat, don't just read the checkmark).** The
gate genuinely evaluated all four dated windows and none crashed the
pipeline — but every window shows exactly 0.0% drawdown because the VRP
gate **never opened a position in any of the four windows at all**, not
because a hedged position weathered the shock. Traced directly, per
window (real True_IVR-proxy/VRP-proxy/VIX/regime readings at each
window's own start date):

| Scenario | Why the gate stayed closed |
|---|---|
| OCT_2008 | VIX already **39.8** at window start (the crisis was already underway before this dated window even begins) — real, correct VIX gating |
| FEB_2018 | True_IVR-proxy 54.5 (clears the 50 threshold) but VRP-proxy **+0.08%**, just under the 2% floor — missed by a hair |
| MAR_2020 | Window starts 2020-02-18, before the crash's vol spike; True_IVR-proxy only 23.0 |
| AUG_2024 | True_IVR-proxy 92.4 (very high) but VRP-proxy **−5.5%** (negative) — the fast-reacting GARCH forecast had already caught up to/exceeded the smoother 60-day trailing level exactly as vol was spiking, the same anti-correlation pattern noted in the module docstring |

This is a genuinely-run, non-fabricated result — `passes_stress_gate`
fails closed on any missing/errored window and none occurred here — but
it should be read as "the gate correctly kept the strategy out of all
four historical shocks," not "a position survived all four shocks." Both
are legitimate risk-management outcomes for a regime-gated strategy, but
they are not the same claim, and CONSTRAINT #4 requires stating which one
actually happened.

**Honest overall read**: `deployable=False` is the correct, unforced
outcome. The strategy's core mechanism — only sell premium when the gate
genuinely clears — worked exactly as designed for staying OUT of the four
dated crash windows, but the one real trade the gate DID approve (April
2022) lost badly, and 21 years of history produced too few trades to
average that single loss away or to say anything statistically strong
about the methodology either way. No threshold was loosened, no window
was cherry-picked, and the honest result (including the vacuous-pass
nuance above) is recorded as-is. See
`docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08 entry for the full
writeup and `tests/test_options_selling_backtest_stress.py`/
`tests/test_validation_vrp_premium_selling_registry.py` for the adapter's
own regression coverage.

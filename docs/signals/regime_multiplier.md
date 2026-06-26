# Signal: `regime_multiplier`

**File:** `signals/regime_multiplier.md`  
**Default weight:** 0.0 (intentionally zero — this module does NOT contribute to the score)  
**Score range:** Always 0.0  
**Regime gate:** Always active  
**Special role:** HMM Kelly-size scalar — affects position sizing only, not signal direction

---

## Rationale

This module has an unusual architecture: it is a `SignalModule` with a weight of exactly
0.0 and a `compute()` method that always returns `score=0.0`. It contributes nothing to
the `SignalAggregator`'s weighted-sum `final_score`.

Its sole purpose is to carry the **HMM regime probability** through the signal pipeline
as a **position-sizing multiplier** — specifically, the `hmm_risk_on_probability` from
`regime/hmm_regime.py`'s Gaussian HMM second opinion. It is accessible via the `outputs`
dict returned by `SignalAggregator.aggregate()`, which `StrategyEngine.evaluate_security()`
reads to scale the Kelly Target:

```python
hmm_confidence = outputs['regime_multiplier'].confidence   # ∈ [0, 1]; default 1.0
kelly_target   = base_kelly * hmm_confidence * meta_label_composite
```

The design keeps the HMM multiplier in the signal pipeline (where it is visible in the
audit trail, logged, and testable) without allowing it to directionally influence the
final score (which would create a double-counting problem with `macro_regime`).

---

## HMM Background

The 3-state Gaussian Hidden Markov Model in `regime/hmm_regime.py` (Hamilton, 1989)
takes 4 features:

1. SPY daily return
2. 20-day realized volatility
3. VIX level
4. 10y−2y yield curve spread

State identification uses `identify_states_by_vol()`: the state with the lowest fitted
variance is labelled "bull", middle is "sideways", highest is "bear". The probability
of being in a bull or sideways state is summed as `risk_on_probability`.

**One-way gate:** The HMM can only pull signals down, never push them up. It can degrade
RISK ON → NEUTRAL (when `hmm_risk_on_probability < 0.30`), and trigger the kill switch
at lower thresholds in RECESSION (when `risk_off > 0.70`), but a bearish rules-based
regime is never upgraded by the HMM.

---

## Signal (Non-)Logic

```python
def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
    # Always returns 0.0 — this module ONLY carries the HMM multiplier
    hmm_proba = (context.macro.hmm_risk_on_probability
                 if context.macro.hmm_risk_on_probability is not None
                 else 1.0)   # 1.0 = neutral / HMM unavailable
    return SignalOutput(
        score=0.0,           # NEVER contributes to final_score
        confidence=hmm_proba,# This is what StrategyEngine reads
        explanation=f"HMM risk-on: {hmm_proba:.2f} (Kelly scalar)"
    )
```

The `confidence` field is repurposed as the Kelly scaling factor. Its value:
- **1.0:** HMM unavailable, or HMM strongly agrees with RISK ON.
- **0.30–0.99:** HMM partially agrees; position sizes scaled proportionally.
- **< 0.30:** HMM strongly disagrees with RISK ON; rules-based regime may be downgraded.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| HMM fails to fit (insufficient SPY history) | `hmm_risk_on_probability = None`; module sets `confidence = 1.0` (neutral). No position size reduction. |
| `hmm_risk_on_probability = 0.0` (extreme risk-off) | Kelly Target multiplied by 0 → zero position size for this ticker in this cycle. The advisory signal (BUY/HOLD) is unchanged — only sizing is affected. |
| Weight accidentally set to non-zero | If `settings.SIGNAL_WEIGHTS["regime_multiplier"]` is accidentally set to a positive value, the module would contribute up to `0.0 × weight = 0.0` pts (since `score` is always 0.0) — the zero score structurally prevents any contribution regardless of weight. The `tests/test_regime_multiplier.py` test confirms `score=0.0` even with an artificially large weight. |

---

## Testing

The key test: even with `weight = 999.0`, the aggregator contribution is exactly `0.0`:

```python
# from tests/test_regime_multiplier.py
def test_zero_contribution_regardless_of_weight():
    aggregator = SignalAggregator(weights={"regime_multiplier": 999.0})
    result = aggregator.aggregate(row, context)
    assert result.final_score == pytest.approx(0.0)
    assert result.outputs["regime_multiplier"].score == 0.0
```

This structural guarantee is more robust than a documentation note.

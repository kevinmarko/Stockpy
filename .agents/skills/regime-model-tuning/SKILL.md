---
name: regime-model-tuning
description: >-
  Tune, diagnose, or reason about the Gaussian HMM regime detector
  (regime/hmm_regime.py), the rules-based macro regime classifier it feeds
  into (macro_engine.py), and the downstream gates that consume their output
  (execution/risk_gate.py's hmm_regime check, technical_options_engine.py's
  VRP gate). Use when adjusting HMM state count/retrain cadence, debugging
  why the HMM is stuck in one state or disagrees with the rules-based
  regime, or investigating a blocked/unblocked BUY tied to macro or
  volatility-regime conditions -- covers what "risk-on/off" actually means
  in the real code, the no-lookahead contract, and the real settings.
---

<!--
  Ported from this repo's Claude Code sibling skill (`.claude/skills/regime-model-tuning/SKILL.md`)
  to Antigravity's skill format. Frontmatter and body content are carried over verbatim --
  Antigravity's own `google-antigravity-sdk` skill and this repo's existing `.agents/skills/supabase`
  skill both use the same minimal `name` + `description` frontmatter shape Claude's SKILL.md already
  used here, so no restructuring was required for this port beyond this note.
-->

# Regime model tuning (HMM + macro regime + VRP gate)

This repo has **three separate, non-interchangeable "regime" mechanisms**.
Confusing them is the most common way this task goes wrong — pin down which
one you're actually touching before changing anything.

| Mechanism | Module | States / output | Role |
|---|---|---|---|
| Rules-based macro classifier | `macro_engine.py` | `market_regime` isin `{"RISK ON", "NEUTRAL", "RECESSION", "CREDIT EVENT"}` | **Primary** regime signal |
| Gaussian HMM regime detector | `regime/hmm_regime.py` | 3 hidden states, variance-ranked → `bull`/`sideways`/`bear`; exposed as `hmm_risk_on_probability` | Statistical **second opinion only** — downgrades/confirms, never independently overrides |
| VRP gate for options-selling | `technical_options_engine.py::OptionsPricingRecommender.generate_strategy_pricing_matrix` | boolean `sell_premium_allowed` | Separate, narrower gate — only decides whether premium-SELLING is allowed this cycle |

`regime/hmm_regime.py`'s own module docstring states this explicitly: *"the
rules-based classification in macro_engine.py / MacroEconomicDTO.market_regime
... remains primary; this module's output (hmm_risk_on_probability) is used
only to downgrade/confirm, never to independently override."* Don't wire the
HMM to independently gate anything — that would contradict the documented
design.

## 1. The HMM regime detector (`regime/hmm_regime.py`)

`HMMRegimeDetector(n_states=3, retrain_freq_days=7, random_state=42)` — a
3-state Gaussian HMM (Hamilton 1989 regime-switching), constructed fresh
every cycle by `MacroEngine.__init__` with `n_states=settings.HMM_N_STATES`
(default `3`) and `retrain_freq_days=settings.HMM_RETRAIN_FREQ_DAYS`
(default `7`).

**Public surface, in call order:**

1. `build_feature_matrix(spy_price_df, vix_series, yield_curve_series)` —
   builds the 4-column feature frame: `spy_return`, `realized_vol_20d`
   (20-day rolling std, annualized ×√252), `vix_level`, `yield_curve_spread`.
   Rows with any NaN are dropped, never fabricated. Indices are normalized
   (tz stripped, time-of-day stripped) so yfinance's tz-aware intraday
   timestamps and FRED's naive-midnight index align on calendar date rather
   than silently all-NaN-joining.
2. `.fit(features_df)` — refits **only if** more than `retrain_freq_days`
   have elapsed since the last real fit; a call within that window is a
   silent no-op (by design — this is what proves no-lookahead under
   `tests/test_hmm_no_lookahead.py`: adding one more day of data does not
   retroactively change a recent fit's distributional fingerprint within the
   same retrain cycle). Standardizes features (zero-mean/unit-std, degenerate
   std guarded to `1.0`) before fitting `hmmlearn.hmm.GaussianHMM(n_components=n_states,
   covariance_type="diag", n_iter=100)`.
3. `.identify_states_by_vol()` — ranks the fitted hidden states by total
   **variance** of `model.covars_`, ascending. For `n_states == 3`: lowest
   variance → `"bull"`, middle → `"sideways"`, highest → `"bear"`. This is
   the entire mechanism by which "risk-on" gets defined — it is a purely
   statistical low-vol/high-vol ranking, not a directional (up/down) label.
4. `.predict_proba(features_df)` — returns `{p_state_0..p_state_{n-1},
   dominant_state, risk_on_probability}`, where `risk_on_probability` is the
   probability mass on whichever state(s) are labeled `"bull"`.

**No-lookahead contract, exactly**: `predict_proba` uses hmmlearn's
`predict_proba()` but returns **only the last row**. The module docstring
proves this is mathematically equivalent to pure forward filtering (not
Viterbi/smoothing, both of which use the full sequence including future
rows) via the forward-backward identity — the guarantee holds *only* because
callers never index into any row but the last one. **If you touch this
module: never let a caller slice past the date it wants a probability for,
and never expose any row but `[-1]`.** This differs from
`processing_engine.py`'s momentum features (which `.shift(1)` because they
predict a *later* bar) — here the classifier legitimately uses the row's own
same-day close, because it's answering "what state are we in as of today's
close", not predicting tomorrow.

**Wiring into the DTO**: `MacroEngine.compute_hmm_risk_on_probability(spy_price_df)`
(`macro_engine.py`) calls the above and returns `Optional[float]` — `None`
(never fabricated) when SPY price history or VIX/yield-curve history is
unavailable. `main_orchestrator.run_pipeline()` passes the SPY tech-raw frame
in; the result lands on `MacroEconomicDTO.hmm_risk_on_probability`, which
`config.COLUMN_SCHEMA`'s `HMM_Risk_On_Probability` column surfaces per-row.

## 2. Where the HMM output actually gates something

`execution/risk_gate.py`'s `hmm_regime` check (`PreTradeRiskGate`, around
line 364): blocks new BUY orders when
`1.0 - context.macro.hmm_risk_on_probability > settings.HMM_RISK_OFF_BLOCK_THRESHOLD`
(default `0.80`) — i.e. risk-off probability above 80%. If `context.macro`
is `None` or `hmm_risk_on_probability` is `None`, the check is skipped
(fail-open on missing data, not fail-closed — consistent with this module's
"never crash on missing telemetry" convention; verify this against the
current source before relying on it, since risk-gate fail behavior is
exactly the kind of thing that gets tightened over time).

This is a **different** gate from the platform's macro kill switch
(`PreTradeRiskGate.macro_kill_switch_check`, gated by
`settings.MACRO_REGIME_GATE_ENABLED`, tripped by Sahm Rule ≥ 0.5, VIX > 30,
or HY OAS > 6% — see CLAUDE.md's Macro Regime Gate bullet) — that one reads
`MacroEconomicDTO.killSwitch`, not the HMM. Don't conflate the two when
diagnosing a blocked BUY; check which check's name (`hmm_regime` vs. the
kill-switch check) actually appears in the risk-gate's rejection reason.

## 3. The VRP gate (options-selling only — separate system)

`OptionsPricingRecommender.generate_strategy_pricing_matrix()`
(`technical_options_engine.py`) enforces, inline, exactly the rule CLAUDE.md
documents: premium-selling (Put/Call Credit Spread, Iron Condor) is allowed
only if `true_ivr > 50`, `vrp > 0.02`, `vix < 30`, and
`macro_dto.market_regime != 'CREDIT EVENT'`. Reading the real gate logic
(lines ~223-235): `sell_premium_allowed` starts `True` and is set `False` if
`vrp <= 0.02` OR (`vix >= 30.0` OR `regime == 'CREDIT EVENT'`). If gated
while `true_ivr > ivr_sell_threshold` (default `50.0`), the function returns
the untouched `{"Strategy": "Cash", "Action": "Wait", ...}` directive rather
than emitting a spread it shouldn't. This gate is entirely independent of
both the HMM and the macro kill switch — it reads `macro_dto.market_regime`
and `macro_dto.vix` directly, not `hmm_risk_on_probability` or `killSwitch`.

## 4. Running/inspecting regime state

```bash
# Full cycle -- exercises macro_engine.py + regime/hmm_regime.py together:
python3 main_orchestrator.py

# Targeted tests (read these before changing fit()/predict_proba() -- they
# encode the no-lookahead contract as executable proof):
pytest tests/test_hmm_no_lookahead.py -v
pytest tests/test_hmm_synthetic.py -v
pytest tests/test_hmm_state_persistence.py -v
pytest tests/test_macro_hmm_integration.py -v
```

## 5. Common failure modes & fixes

**HMM appears "stuck" in one state despite an obvious market shift.**
Before assuming the model is broken, check `retrain_freq_days` — a call to
`.fit()` within `HMM_RETRAIN_FREQ_DAYS` (7) of the last real fit is a
documented no-op, not a bug. If more than 7 days have genuinely elapsed and
it's still not transitioning, the more likely cause is the input feature
history: `build_feature_matrix` drops any row with a NaN in any of its 4
columns, so a gap in VIX or yield-curve history (not just SPY) silently
shrinks the fitted window. Confirm `vix_series`/`yield_curve_series`
coverage before touching `n_states` or the transition structure.

**"Risk-on" state doesn't match intuition (e.g. HMM calls a rally
`"sideways"` or `"bear"`).** This is expected and by design, not a bug —
`identify_states_by_vol()` ranks states by **variance alone**, not by mean
return or direction. A genuinely volatile rally can rank as a higher-variance
state than a calm selloff. Don't "fix" this by relabeling based on mean
return; that would break the Hamilton (1989) variance-regime framing the
whole module is built on. If the operator wants a directional regime read,
point them at `macro_engine.py`'s rules-based `market_regime` instead — that
IS the primary, directional classifier this module is explicitly scoped to
support, not replace.

**A BUY is unexpectedly blocked and it's unclear which gate did it.** Check
the risk-gate rejection reason string first — `"hmm_regime"` means §2's HMM
check fired; a macro-kill-switch reason means §2's *other* check fired;
neither means the block is coming from somewhere else in the pipeline
entirely (e.g. sizing, portfolio heat). Don't guess — read
`execution/risk_gate.py`'s `run_all()` output for the exact failing check
name before changing any threshold.

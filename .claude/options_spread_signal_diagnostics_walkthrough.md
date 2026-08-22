# Walkthrough — options-spread insufficient-signal diagnostics

## What was wrong

`python -m scripts.refresh_validations --strategies put_credit_spread,call_credit_spread`
reproducibly returned `deployable=false, pbo=NaN, dsr=NaN, sharpe=null, max_drawdown=0.24`
with no explanation, and the options-selling tail-scenario stress gate reported a trivially
green `GATE: PASS` at exactly 0.0% drawdown across all 4 dated crisis windows.

## Root cause (confirmed by direct reproduction, not guessed)

`technical_options_engine.py::generate_strategy_pricing_matrix()` only emits a Put/Call Credit
Spread or Iron Condor when **five** conditions hold at once: `true_ivr > 50`,
`VRP_proxy > OPTIONS_VRP_THRESHOLD`, `VIX < 30`, not `CREDIT EVENT`, and a matching
`trend_bias`. Reproduced offline against this machine's real cached SPY/macro history
(`~/.stockpy_local/quant_platform.db`) and independently cross-checked over the full
2005-2026 window via `yfinance`:

- `true_ivr` is **not** chronically biased low — it correctly spikes to 100 on real crisis
  dates (2018-02-13 Volmageddon, 2020-03-17 COVID).
- But `VRP_proxy = trailing_60d_realized_vol − garch_vol` and `true_ivr` (a rank of that SAME
  `garch_vol` against its own trailing range) are **structurally anti-correlated**
  (measured `corr = -0.216`): a real vol spike pushes `garch_vol` up, which drives `true_ivr`
  UP and `VRP_proxy` DOWN simultaneously. The two gates rarely clear together.
- The pool of SPY cycles that ever clear the full 5-condition gate is tiny — roughly 2-3
  across 21+ years, measured consistently across two independent data sources/periods.
- `put_credit_spread`/`call_credit_spread`'s raw per-day return series therefore ends up
  (effectively) all exactly `0.0` across a ~20-year window. `_apply_cost_model`'s flat
  per-day cost then produces a numerically-constant series whose `std()` trips
  `sharpe_ratio`'s existing `< 1e-12` degenerate-std guard → NaN Sharpe → NaN PBO/DSR.
  Compounding that same constant cost drag over ~20 zero-trading years independently
  reproduces the reported `max_drawdown≈0.24` — a cost-drag artifact, not a real loss.

An initial hypothesis during this investigation (based only on 2015-2026 locally cached
data) — that `iron_condor`/`vrp_premium_selling` is "equally starved," contradicting this
repo's own characterization of it as "validates fine" — was **checked against the full
2005-2026 window and retracted**. This repo's own validation record shows
`vrp_premium_selling` genuinely validating (Sharpe=0.217, DSR=0.999, non-degenerate) over
that window, and the full-window cycle-plan check confirms why: Iron Condor gets 1-2 real
trading cycles (comfortably enough real variance to clear the degenerate-std guard) where
Put/Call Credit Spread got 0. This is small-sample noise in how the tiny qualifying pool
splits three ways by `trend_bias`, not an asymmetric bug — see the doc entry for the full,
corrected write-up.

## What changed

1. **`validation/metrics.py`** — new `describe_signal_sparsity(returns)`, gated on the exact
   same degenerate-std condition `sharpe_ratio` checks, explains a NaN Sharpe/PBO/DSR with
   the real non-zero-observation count.
2. **`validation/harness.py`** — computes the note from the RAW (pre-cost-model) test
   returns, threads it onto `ValidationReport.signal_sparsity_note`, `to_summary_dict()`,
   and the rendered HTML report.
3. **`validation/stress_scenarios.py`** — a non-empty but all-zero window now fails closed
   via `error` (routing through the existing fail-closed `StressResult.passed` logic)
   instead of a trivial 0%-drawdown PASS.
4. **`scripts/refresh_validations.py`** — logs the note in the CLI output.
5. **`reports/validation_report_template.html.j2`** — renders the note in the HTML report.

## Verification

- Full offline suite, targeted at the affected areas:
  `pytest -k "metrics or harness or stress or options_selling" -m "not network"` →
  **396 passed, 0 regressions**.
- Network-marked stress-gate tests (live yfinance data was actually reachable in this
  sandbox, so these were genuinely re-run, not just left `@pytest.mark.network`-skipped):
  `pytest tests/test_options_selling_backtest_stress.py::test_full_stress_gate_runs_end_to_end_for_all_options_selling_strategies tests/test_stress_runner.py -m network`
  → **9 passed**.
- End-to-end confirmation against real cached data: `describe_signal_sparsity` on
  `put_credit_spread`'s real 2015-2026 returns now reports
  `"insufficient trading signal: 0/2929 observations were non-zero..."`; the stress gate
  now reports `GATE: FAIL` with each of the 4 dated windows individually explained as
  `(no real trading signal in window (strategy never entered a position))` instead of a
  trivial PASS.

## Documentation

- `docs/VALIDATION_STRATEGY_FIX_LOG.md` — new 2026-08-22 entry with the full root-cause
  write-up, the corrected item-2 finding, and before/after evidence.
- `docs/architecture/validation-and-signals.md` — `validation/metrics.py`,
  `validation/harness.py`, `validation/stress_scenarios.py` bullets extended.

## Explicitly not done

Per the request, item 2 (whether the 1-2/239 firing rate is itself a bug) is **flagged, not
fixed** — no change was made to `generate_strategy_pricing_matrix()`'s gate thresholds or
`_compute_cycle_plan()`'s proxy formulas. Whether `ivr_sell_threshold=50` /
`OPTIONS_VRP_THRESHOLD=0.02` / `VIX<30` are miscalibrated for SPY, or SPY genuinely offers
this few "clean" premium-selling entries in 21 years, is an open question for a human
decision.

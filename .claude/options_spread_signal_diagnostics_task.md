# Task tracker — options-spread insufficient-signal diagnostics

Branch: `fix-options-spread-signal-diagnostics`

## Item 1 — make the failure self-diagnosing (implemented)

- [x] `validation/metrics.py::describe_signal_sparsity()` (+ shared `_DEGENERATE_STD` constant)
- [x] `validation/harness.py`: thread `raw_test_returns` → `signal_sparsity_note` onto
      `ValidationReport` / `to_summary_dict()` / `_render_html_report`
- [x] `validation/stress_scenarios.py::run_stress_scenario`: zero-signal-in-window fails closed
      via `error` instead of a trivial 0%-drawdown PASS
- [x] `scripts/refresh_validations.py::_validate_single_strategy`: log the note in the CLI
- [x] `reports/validation_report_template.html.j2`: render the note in the HTML report
- [x] Tests: `tests/test_metrics_sharpe_ratio.py::TestDescribeSignalSparsity` (5),
      `tests/test_harness_signal_sparsity.py` (4, new file),
      `tests/test_stress_runner.py::test_runner_records_error_when_returns_fn_yields_all_zero_signal` (1),
      `tests/test_options_selling_backtest_stress.py::test_full_stress_gate_runs_end_to_end_for_all_options_selling_strategies`
      (relaxed assertion for the new legitimate error case)
- [x] Full offline suite re-run (`-k "metrics or harness or stress or options_selling" -m "not network"`):
      396 passed, 0 regressions
- [x] Network-marked stress-gate tests actually re-run live in this sandbox (yfinance worked):
      9/9 passed

## Item 2 — investigate the 1-2/239 firing rate (flag only, per the request — not resolved here)

- [x] Measured `true_ivr` proxy against real crisis dates (2018-02-13, 2020-03-17) — confirmed
      NOT chronically biased low; correctly spikes to 100
- [x] Measured `VRP_proxy` alongside `true_ivr` proxy — `corr = -0.216`, structural
      anti-correlation identified and explained (both derive from the same `garch_vol`)
- [x] Cross-checked `iron_condor`/`vrp_premium_selling` — initially (2015-2026-only local data)
      concluded "equally starved, contradicts CLAUDE.md's 'validates fine'"; **corrected** after
      checking the full 2005-2026 window (via yfinance) and this repo's own prior validation
      record (`vrp_premium_selling` genuinely validates, Sharpe=0.217/DSR=0.999) — the earlier,
      narrower conclusion was retracted in the docs, not left standing
- [x] Final, better-supported finding written up plainly in
      `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08-22 entry: the qualifying-cycle pool is
      real and tiny (~2-3 across 21+ years), and which strategy gets 0/1/2 of them is
      small-sample noise, not an asymmetric bug — open question (threshold calibration vs.
      genuine SPY behavior) explicitly left to a human decision

## Documentation (per CLAUDE.md's mandatory doc-update step)

- [x] `docs/VALIDATION_STRATEGY_FIX_LOG.md` — new 2026-08-22 entry (item 1 fix + item 2 findings)
- [x] `docs/architecture/validation-and-signals.md` — `validation/metrics.py`,
      `validation/harness.py`, `validation/stress_scenarios.py` bullets extended
- [x] Confirmed no `docs/signals/put_credit_spread.md`/`call_credit_spread.md` exist (this
      strategy family is validation-only, not `SignalModule`s) — noted rather than inventing
      new files that don't match this repo's existing convention

## Not done (out of scope, per the request)

- Did NOT change `technical_options_engine.py`'s gate thresholds (`ivr_sell_threshold`,
  `OPTIONS_VRP_THRESHOLD`, `VIX<30`) or `_compute_cycle_plan`'s proxy formulas — item 2 was
  explicitly "flag for a human decision, don't just fix unilaterally"

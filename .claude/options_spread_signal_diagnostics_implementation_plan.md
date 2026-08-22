# Self-diagnosing insufficient-signal reporting for put_credit_spread / call_credit_spread

## Context

`python -m scripts.refresh_validations --strategies put_credit_spread,call_credit_spread`
returns `deployable=false, pbo=NaN, dsr=NaN, sharpe=null, max_drawdown=0.24` for both
strategies, reproducibly. The options-selling stress gate also reports a trivial
`GATE: PASS` at exactly 0.0% drawdown across all 4 dated crisis windows — a false-green
result, since a real strategy essentially never shows exactly 0% in every historical
crisis window.

**Root cause, confirmed by direct reproduction against this machine's real, locally
cached FMP-sourced SPY/macro history (`~/.stockpy_local/quant_platform.db`, 2015-2026,
covering Volmageddon/COVID/2022)** — not guessed:

- `technical_options_engine.py::generate_strategy_pricing_matrix()` only emits
  `"Put Credit Spread"`/`"Call Credit Spread"`/`"Iron Condor"` when **five** conditions
  hold simultaneously: `true_ivr > 50`, `VRP > OPTIONS_VRP_THRESHOLD` (0.02), `VIX < 30`,
  not `CREDIT EVENT`, and a matching directional (or neutral, for Iron Condor)
  `trend_bias`.
- Measured directly (`validation/options_selling_backtest.py::_compute_cycle_plan`,
  offline, real data, no live network needed): over 127 monthly cycles (2015-2026),
  `true_ivr` (a min-max realized-vol-rank proxy over GARCH vol) crosses 50 on 24 cycles
  (18.9%) — it correctly spikes to 100 on real crisis dates (2018-02-13 Volmageddon,
  2020-03-17 COVID). **But of those 24, only 1 also had `VRP_proxy > 0.02`.** Measured
  `corr(true_ivr_proxy, VRP_proxy) = -0.216` across the whole window, and VRP was
  negative in 23/24 high-IVR cycles.
  - This is structural, not random: `VRP_proxy = trailing_60d_realized_vol − garch_vol`,
    while `true_ivr` ranks that SAME `garch_vol` against its own trailing range. A real
    vol spike pushes `garch_vol` up sharply — which simultaneously drives `true_ivr` UP
    (near its own trailing high) and `VRP_proxy` DOWN (since the fast-reacting GARCH
    forecast now exceeds the slower 60-day trailing realized-vol average that VRP
    subtracts it from). The two gates are built from the same input in opposing
    directions, so they rarely clear together.
  - Consequence for the actual reported numbers: with (effectively) zero real trading
    days across a ~20-year window, `put_credit_spread`'s raw per-day return series is
    all exactly `0.0`. `StrategyValidationHarness._apply_cost_model` then subtracts a
    **constant** per-day turnover cost from every day regardless of whether a trade
    occurred, producing a numerically-constant series whose `std()` lands near (not
    exactly) 0.0 from float noise — `validation/metrics.py::sharpe_ratio`'s existing
    `< 1e-12` degenerate-std guard correctly fires → NaN Sharpe → NaN DSR/PBO (this
    exact mechanism — a flat-cost-deduction book producing a near-zero-but-nonzero std
    — is already a documented, tested bug class in this repo, see
    `tests/test_metrics_sharpe_ratio.py`'s docstring, the PR #501 "Regime Navigator"
    incident). Compounding that same constant per-day cost drag over ~4900 zero-trading
    days independently reproduces the reported `max_drawdown≈0.24` almost exactly (a
    pure cost-drag artifact of a strategy that never actually opened a position, not a
    real loss).
  - **The exact same starvation affects `vrp_premium_selling`/`iron_condor`**, which
    shares this identical cycle-plan/proxy computation: measured only 1 real Iron Condor
    fire in the same 127 cycles (2015-2026, including COVID and Volmageddon). This
    directly contradicts this repo's own `CLAUDE.md` characterization that
    `vrp_premium_selling`/`iron_condor` "validates fine ... isn't gated to one specific
    trend direction" — worth flagging plainly (not silently fixed) since re-deriving why
    that doc statement was made is out of scope here.

**Decision on the two items from the request:**
1. **Make the failure self-diagnosing** (implement now) — surface *why* Sharpe/PBO/DSR
   came back NaN, and fix the stress gate's trivial 0%-drawdown false-PASS, mirroring
   the precedent commit `1791c701` ("make 'insufficient history' error self-diagnosing").
2. **Whether the 1-2/239 firing rate is itself a bug** (flag only, per the request) —
   evidence above shows the *true_ivr proxy itself is not biased low* (it correctly
   spikes at real crisis dates); the rarity is a structural consequence of the
   VRP-proxy/true_ivr-proxy anti-correlation in the *gate combination*, which also
   equally starves `iron_condor`/`vrp_premium_selling`. This is written up plainly in
   the docs (see below) as an evidence-backed, unresolved finding for a human decision —
   not something this change resolves unilaterally.

## Implementation

### 1. `validation/metrics.py` — explain a NaN Sharpe caused by zero real signal

Add, next to `sharpe_ratio` (reuse a new local `_DEGENERATE_STD = 1e-12` constant
instead of the two independent inline `1e-12` literals currently in that function):

```python
def describe_signal_sparsity(returns: Optional[pd.Series]) -> Optional[str]:
```

- Returns `None` immediately unless `returns` is non-empty AND its `std()` is NaN or
  `< _DEGENERATE_STD` — i.e. it is **causally gated on the exact same condition that
  produces the NaN Sharpe**, so it never false-positives on a legitimately
  low-frequency-but-real strategy (e.g. `pairs_trading`).
- When gated in: counts `n_nonzero` (`abs(value) > 1e-9`, dropping NaN) vs `n_total`.
  - `n_nonzero == 0` → `"insufficient trading signal: 0/{n_total} observations were
    non-zero over this backtest window -- the underlying strategy never actually
    produced a priced trade (every day degraded to a 0.0 fill), so
    Sharpe/PBO/DSR/MaxDD are not a genuine measurement of strategy skill or risk."`
  - `n_nonzero > 0` (rare edge case: some real but collectively negligible-variance
    observations) → same framing, naming the actual `n_nonzero/n_total` count.
- Never raises (`try/except`, diagnostic-only — matches
  `scripts/refresh_validations.py::_describe_universe_coverage`'s existing
  never-raises convention).

### 2. `validation/harness.py` — thread the note onto `ValidationReport`

- Import `describe_signal_sparsity` alongside the existing
  `from validation.metrics import (run_cpcv_evaluation, sharpe_ratio, ...)` line.
- In `run()`, step 5 (`full_trials = self.strategy_fn(X, y, X, y)`): capture
  `raw_test_returns = best_trial["test_returns"]` when `full_trials` is truthy, else
  `None`. Must be the **pre-cost-model** series — `_apply_cost_model` subtracts a
  constant, which doesn't change `std()` (so the NaN-gate still fires correctly) but
  *would* make literally every zero-fill day read as "nonzero" for the count, defeating
  the diagnostic's own numbers.
- Compute `signal_sparsity_note = describe_signal_sparsity(raw_test_returns)` once,
  after `full_returns`/`sharpe` are computed.
- Add `signal_sparsity_note: Optional[str] = None` to `ValidationReport.__init__`
  (stored as `self.signal_sparsity_note`), pass it through the `ValidationReport(...)`
  construction in `run()`, and add it to `to_summary_dict()`'s returned dict (a plain
  additive JSON key — no existing consumer reads a strict schema that would break).

### 3. `validation/stress_scenarios.py` — stress gate fails closed instead of trivial-passing

In `run_stress_scenario`, immediately after the existing "no data in window" check
(`returns is None or len(returns) == 0`), add: if `returns` is non-empty but **every**
value is `abs(value) <= 1e-9` (inline check, no new shared helper needed — this is a
different question than `describe_signal_sparsity` above: "did the strategy ever hold a
position in this window," not "why did Sharpe go NaN"), return a `StressResult` with
`error="no real trading signal in window (strategy never entered a position)"`,
`survived=False`, `max_drawdown`/`final_return=NaN`, `n_days=<real day count>`. This
routes through the **existing** `StressResult.passed` / `passes_stress_gate` fail-closed
logic (`error is not None → False`) with no changes needed to either of those.

### 4. `scripts/refresh_validations.py` — surface the note in the CLI log

In `_validate_single_strategy`, right after the existing
`logger.info("  %-32s deployable=...")` line, add a conditional follow-up line:
```python
if summary.get("signal_sparsity_note"):
    logger.info("    ⚠ %s", summary["signal_sparsity_note"])
```

### 5. `reports/validation_report_template.html.j2` — surface the note in the rendered report

- `validation/harness.py::_render_html_report`: add `signal_sparsity_note=report.signal_sparsity_note`
  to the `template.render(...)` call.
- Template: a small `{% if signal_sparsity_note %}` note block (reusing the existing
  `--danger-color`/`.card` styling already used by the stress-test section), placed
  right after `<header>` and before the `{% if is_options_selling %}` stress section so
  it's visible for every strategy type, not only options-selling ones.

### 6. Tests

- `tests/test_metrics_sharpe_ratio.py`: new `TestDescribeSignalSparsity` class —
  all-zero → the "0/N" message; dense real returns → `None`; genuinely-empty →
  `None`; a constructed near-zero-but-nonzero-std case → the "n_nonzero/n_total"
  message. Mirrors this file's existing `TestDegenerateStdGuard` style.
- New `tests/test_harness_signal_sparsity.py` (mirrors
  `tests/test_harness_calmar_degenerate_guard.py`'s offline-harness pattern: autouse
  fixture stubbing `get_universe_with_survivorship_warning`/`_spy_return_series`,
  synthetic `X`/`y`, a fixed `strategy_fn`): all-zero train/test returns →
  `report.signal_sparsity_note` mentions "insufficient trading signal" and "0/"; dense
  random returns → `report.signal_sparsity_note is None`.
- `tests/test_stress_runner.py`: new
  `test_runner_records_error_when_returns_fn_yields_all_zero_signal` — a non-empty,
  all-`0.0` `returns_fn` → `res.error is not None`, `res.survived is False`,
  `res.passed is False`. Verified the existing tests in this file
  (`test_runner_executes_on_synthetic_returns`'s `+0.1%/day` constant-but-genuinely-nonzero
  fixture, `test_runner_records_error_when_returns_fn_yields_no_data`'s *empty* series)
  are unaffected by the new check.
- `tests/test_options_selling_backtest_stress.py`:
  `test_full_stress_gate_runs_end_to_end_for_all_options_selling_strategies` currently
  asserts `result.error is None` unconditionally for every strategy/window — this will
  now legitimately fire for `put_credit_spread`/`call_credit_spread`/`iron_condor` in
  windows where they never traded. Relax the assertion to accept `error` **only** when
  it is the new "no real trading signal" message (still failing the test on any other,
  genuine data-gap error). This test is `@pytest.mark.network` and cannot be executed
  live in this sandbox (no market-data network access) — call this out explicitly in
  the PR/walkthrough as unverified-live, consistent with this repo's existing disclosed
  sandbox limitation.
- Run the full offline suite (`pytest -m "not network"`, or targeted files above) to
  confirm zero regressions elsewhere.

### 7. Docs (mandatory per `CLAUDE.md`'s Implementation-Plan documentation step)

- `docs/VALIDATION_STRATEGY_FIX_LOG.md`: new dated entry — (a) the self-diagnosing fix
  description mirroring commit `1791c701`'s write-up style; (b) the stress-gate
  trivial-pass fix; (c) the full item-2 investigation write-up (measured true_ivr/VRP
  numbers, the structural anti-correlation explanation, the `iron_condor` cross-check,
  and the explicit note that `CLAUDE.md`'s "validates fine" characterization for
  `iron_condor`/`vrp_premium_selling` is contradicted by this measurement and is flagged
  for human review, not silently rewritten). No `docs/signals/put_credit_spread.md` /
  `call_credit_spread.md` exist today (confirmed — these strategies are validation-only
  `STRATEGY_REGISTRY` adapters, not `SignalModule`s with their own doc, and prior
  `VALIDATION_STRATEGY_FIX_LOG.md` entries already cover them the same way) — note that
  explicitly rather than inventing new doc files that don't match this repo's existing
  convention for this strategy family.
- `docs/architecture/validation-and-signals.md`: brief addition documenting
  `describe_signal_sparsity` (`validation/metrics.py`) and `ValidationReport.signal_sparsity_note`
  (`validation/harness.py`), and the stress-gate zero-signal fail-closed behavior
  (`validation/stress_scenarios.py`).

### 8. Branch / PR workflow (per `CLAUDE.md`)

- This is a `validation/` change → feature branch (e.g.
  `fix-options-spread-signal-diagnostics`), PR required, never direct to `main`.
- PR artifacts under `.claude/`, uniquely named per the repo's collision-avoidance rule:
  `.claude/options_spread_signal_diagnostics_implementation_plan.md`,
  `_task.md`, `_walkthrough.md`.

## Verification

- `PYTHONPATH=. /Users/kevinlee/Stockpy-live/.venv/bin/python3 -m pytest tests/test_metrics_sharpe_ratio.py tests/test_harness_signal_sparsity.py tests/test_stress_runner.py tests/test_harness_calmar_degenerate_guard.py tests/test_options_selling_backtest_stress.py -m "not network" -q`
  (this worktree has no `.venv` of its own; the main checkout's interpreter is reused
  with `PYTHONPATH=.` set to this worktree, the same approach used during investigation).
- Re-run the offline diagnostic script used during investigation
  (`_get_cycle_plan`/`_compute_cycle_plan` against the real cached
  `~/.stockpy_local/quant_platform.db` SPY/macro history) after the fix, confirming
  `put_credit_spread`'s harness report now carries a populated `signal_sparsity_note`
  and the stress gate no longer reports a trivial all-window PASS.
- `make verify` / the repo's standard offline gate is out of scope to run in full here
  (network-dependent portions), but the targeted test files above are the load-bearing
  check for this change.

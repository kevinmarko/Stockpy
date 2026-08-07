---
name: strategy-validation
description: >-
  Run and interpret a strategy validation pass through validation/harness.py
  (PBO/DSR/Sharpe/MaxDD deployability gate) and document the result. Use
  when asked to validate a signal/strategy, fix a failing STRATEGY_REGISTRY
  deployability gate, re-run scripts/refresh_validations.py, or interpret a
  validation report's PBO/DSR/Sharpe/MaxDD numbers -- covers the gate
  thresholds, the options-selling tail-scenario stress-gate addendum, and the
  mandatory two-place documentation step (docs/signals/<name>.md's Backtest
  Validation section PLUS docs/VALIDATION_STRATEGY_FIX_LOG.md), including for
  an honest deployable=False outcome.
---

# Running and documenting a strategy validation pass

This repo ran a 2026-07 effort bringing 12 of 13 `STRATEGY_REGISTRY` entries
to a deployable verdict (`docs/VALIDATION_STRATEGY_FIX_LOG.md`). This skill
encodes that workflow as a runnable playbook so a repeat pass (new strategy,
regression check, or an honest-failure investigation) doesn't have to
re-derive it from scratch.

## 1. The gate, exactly

`validation/thresholds.py` is the single source of truth — never hard-code
these numbers elsewhere, and never loosen them to force a pass:

| Metric | Threshold | Direction |
|---|---|---|
| PBO (Probability of Backtest Overfitting) | `< 0.50` | lower is better |
| DSR (Deflated Sharpe Ratio) | `> 0.95` | higher is better |
| Net-of-cost Sharpe | `> 0.50` | higher is better |
| Max Drawdown | `< 0.30` (30%) | lower is better |

`ValidationReport.deployable` (`validation/harness.py:262`) is `True` **iff
all four pass** — see the property's own docstring for the exact boolean
composition (`pbo_pass and dsr_pass and sharpe_pass and max_dd_pass and
self.stress_gate_passed`). `sharpe`/`max_dd` failing gates also fail closed on
`NaN` (`np.isnan(...)` guard), never silently pass a broken computation.

**Options-selling addendum** (`validation/harness.py:191`,
`validation/stress_scenarios.py`): any strategy selling premium (Put Credit
Spreads, Iron Condors, ...) needs a 5th gate — construct the harness with
`is_options_selling=True` and a `stress_returns_fn: Callable[[str, str],
pd.Series]` (start, end) → daily strategy returns, replayed across four
dated shock windows (`OCT_2008`, `FEB_2018`, `MAR_2020`, `AUG_2024` —
`validation/stress_scenarios.py`). Deployable requires max drawdown `< 50%`
(`STRESS_MAX_DRAWDOWN`, `validation/thresholds.py`) **and** account survival
in **every** window — `passes_stress_gate()` fails closed if
`stress_returns_fn` is omitted (an options-selling strategy that was never
stress-tested is never deployable, no exceptions). The stress summary prints
at the top of the validation report (`format_stress_summary`,
`validation/harness.py:1038`).

## 2. Run a validation pass

**The real, adapter-backed workflow** is `scripts/refresh_validations.py`,
not `validation/harness.py`'s own bare CLI. `harness.py`'s `main()`
(`validation/harness.py:1098`) is a thin demo wired to a hardcoded
buy-and-hold-SPY `strategy_fn` — useful for sanity-checking the harness
mechanics in isolation, not for validating a real `STRATEGY_REGISTRY`
strategy (`--strategy <name>` there is just a report label, not a registry
lookup):

```bash
python3 -m validation.harness --strategy <name> --start YYYY-MM-DD --end YYYY-MM-DD
```

For an actual registered strategy, use `scripts/refresh_validations.py`
(`STRATEGY_REGISTRY: Dict[str, Tuple[adapter_fn, turnover, universe]]` at
`scripts/refresh_validations.py:2207` — check this dict for the exact
strategy id and its `(adapter_fn, avg_daily_turnover, universe_tickers)`
tuple before running):

```bash
python -m scripts.refresh_validations --strategies <name> \
  --start 2005-01-01 --end 2026-08-01
# all registered strategies, machine-readable summary:
python -m scripts.refresh_validations --json
```

Key flags (`scripts/refresh_validations.py:2674`'s `main()`): `--strategies`
(comma-separated, default = all keys in `STRATEGY_REGISTRY`), `--start`/`--end`
(default `2005-01-01`..today), `--output-dir` (default `reports/`),
`--n-cpcv-splits`/`--n-test-splits` (CPCV configuration — leave at the
defaults, 10/2, unless you have a specific reason to change the walk-forward
split geometry, since PBO/DSR are sensitive to it), `--json` (prints one
machine-readable JSON line — `{strategy_id: {deployable, pbo, dsr, sharpe,
max_drawdown[, error]}}` — as the *last* line of stdout, after the human
table). Exit code is `1` if any strategy errored or failed to deploy, `0` if
all passed — usable as a CI/preflight gate.

Note the repo has no live-market network access in this sandboxed dev/CI
environment (documented repeatedly across CLAUDE.md's fix entries) — a real
re-run against fresh data requires an environment with that access. Don't
claim a re-verification happened if it didn't; say so honestly in the
documentation step (§4) exactly as the existing entries do (e.g. the ETF
transmission sizing bullets' "no live-market network access, so no
`validation/harness.py` re-verification could be run").

## 3. Interpreting a failure — what each metric failing actually means

Don't reach for a generic fix; the causal levers already proven in this
codebase (`docs/VALIDATION_STRATEGY_FIX_LOG.md`'s "Fix levers used, by
category" section) map fairly directly to *which* gate is failing:

- **MaxDD failing, other gates fine** → the book has no de-risking mechanism
  ahead of a sustained downtrend. The proven, reusable fix is a **Faber
  (2007) SMA-200 trend gate**: zero exposure whenever
  `close < close.rolling(200).mean()` (or `SPY < SPY.rolling(200).mean()` for
  a multi-name book — add `SPY` as a benchmark-only input to the
  `STRATEGY_REGISTRY` universe tuple if it isn't already there). Apply it
  identically to **every variant** the adapter emits — the harness reports
  whichever variant has the best in-sample Sharpe, so one ungated variant
  sitting next to a gated one will still win and still fail. A bare SMA-200
  gate sometimes isn't enough on its own (`coppock_momentum` needed a
  dual SMA-50/200 "golden cross" confirmation to actually close the gap —
  see the fix log for why a single moving average let a choppy topping
  process re-enter the position too early).
- **PBO failing** → near-duplicate strategy variants are making the
  best-in-sample-variant selection effectively random noise across CPCV
  paths. **Measure pairwise correlation between variants before assuming
  which to drop** — the fix log's own worked examples found the intuitively
  "obviously distinct" variant pairing sometimes measures *worse* than a
  near-duplicate pairing, because different-parameter momentum signals can
  dominate in different historical regimes, exactly what PBO is built to
  penalize. A genuinely single variant structurally yields `PBO=0.0`,
  `DSR=1.0` (`n_trials=1`), so collapsing to the strongest single
  variant is always available as a last resort — but only if that variant
  independently clears the Sharpe/MaxDD gates on its own (see below).
- **Sharpe failing, PBO/DSR/MaxDD otherwise fine** → often not fixable by a
  gate or variant change at all. `rsi14_extremes`/`rsi2_mean_reversion`/
  `dividend_yield_edgar_pit`/`deep_value_edgar_pit` all stayed honestly
  `deployable=False` in the 2026-07 series after genuine investigation —
  the edge itself is weak net of realistic transaction costs, or (for the
  EDGAR PIT strategies) the real point-in-time fundamentals coverage is too
  sparse across the backtest window. **Do not loosen the 30/70 RSI
  thresholds, or any other well-known rule's parameters, to chase a passing
  number** — that defeats the point of testing that specific, named rule
  (see `docs/signals/rsi_extremes.md`'s Backtest Validation section for the
  fix log's own statement of this line).
- **Turnover-driven cost drag masquerading as a Sharpe failure** → the cost
  model (`execution.cost_model.TieredCostModel`, mandatory per CLAUDE.md — no
  static cost assumptions) scales with the adapter's declared average daily
  turnover (the second element of the `STRATEGY_REGISTRY` tuple). Before
  assuming the edge is weak, check whether that turnover number is a stale
  copy-pasted default rather than an empirically measured value — several
  2026-07 fixes were exactly this: measure the *actual* mean daily
  `sum(|Δweight|)` from the real backfilled data the adapter uses (EDGAR PIT
  fixes measured against the real backfilled DB, not assumed), and correct
  the registry tuple's turnover value to match. A too-high assumed turnover
  overstates costs and can fail Sharpe for a genuinely infrequently-rebalanced
  strategy.
- **A gated variant with real drag but low exposure** → be aware the cost
  model as implemented charges turnover-derived cost against *every calendar
  day*, not just days a position is actually held — this structurally
  penalizes low-exposure trend-filtered variants relative to
  always-in-the-market ones even when the trend filter genuinely reduces
  risk (see `docs/signals/rsi_extremes.md`'s Backtest Validation section for
  the full mechanic). This is a known, documented harness characteristic —
  don't "fix" it by changing the cost model to chase one strategy's number.

## 4. Mandatory documentation — two places, every time

Whether the outcome is a successful fix or an honest, evidence-backed
`deployable=False`, both of the following are required (CLAUDE.md: *"When a
`STRATEGY_REGISTRY` adapter is changed... add a Backtest Validation
section... and append an entry to
`docs/VALIDATION_STRATEGY_FIX_LOG.md`"*). Skipping either for a "the fix
didn't work" outcome is the one mistake this rule exists to prevent — an
honest failure is not a reason to skip documenting it.

### 4a. `docs/signals/<name>.md`'s `## Backtest Validation` section

Only add this to a signal that has a live `signals/<name>.py` module (some
`STRATEGY_REGISTRY` entries — the EDGAR PIT strategies, `macro_regime_pit` —
are narrower honest proxies of a live signal rather than the signal itself;
document under the *signal's* doc file, referencing the proxy relationship,
exactly as `docs/signals/rsi_extremes.md` does for `rsi14_extremes`). Follow
the exact structure of `docs/signals/rsi_extremes.md`'s own Backtest
Validation section (its final section, dated):

```markdown
## Backtest Validation (`<strategy_registry_id>`, YYYY-MM)

<Prose: what was tried, what worked or didn't, and — critically — the
CAUSAL MECHANISM traced for why, not just the before/after numbers. For an
honest deployable=False outcome, state the measured, evidence-backed reason
the edge doesn't clear the gate (a real data-coverage ceiling, a genuinely
weak net-of-cost edge measured across every construction tried, etc.) — not
"needs more tuning.">

| Metric | Value | Gate |
|---|---|---|
| Sharpe | X.XXX | needs > 0.50 — PASS/FAIL |
| PBO | X.XXX | < 0.50 PASS/FAIL |
| DSR | X.XXX | needs > 0.95 PASS/FAIL |
| MaxDD | XX.X% | < 30% PASS/FAIL |
| `deployable` | **True/False (honest)** | |

**Verdict:** <one paragraph, plain statement of the outcome and why the fix
lever chosen was the right one — or why nothing tried closed the gap.>

See [PR #NNN](...) and
[`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md)
for <cross-reference to the broader series this fix was part of, if any>.
```

### 4b. `docs/VALIDATION_STRATEGY_FIX_LOG.md` entry

This file's header states the rule in full — read it before writing an
entry (`docs/VALIDATION_STRATEGY_FIX_LOG.md:1`): thresholds are never
loosened, filters are never date-snooped to a specific crash window, and a
strategy that genuinely can't clear the gate reporting `deployable=False` is
a **correct, honest outcome**, not a failure to hide. Every fix must be a
fixed, causal, uniformly-applied rule — never a threshold edit, a lookahead,
or a cherry-picked parameter.

Append (don't rewrite existing history) a dated section following the
existing entries' shape:

- A **before/after table** — same four columns (Sharpe/PBO/DSR/MaxDD) plus
  `deployable`, one row per strategy touched in this pass, exactly like the
  "Starting state" / "Final state" tables at the top of the file.
- **"Fix levers used, by category"** — name the causal lever (Category A:
  Faber SMA-200 trend gate; Category B: variant-count reduction backed by
  measurement; Category C in the existing log: empirically-measured turnover
  correction; or a new category if you used something genuinely different),
  and for each strategy touched, one paragraph on *why that lever*, quoting
  the specific measured numbers that justified it (e.g. "measured at
  0.98-correlated under the shared gate" — a claim, not an assumption).
- For any strategy that stayed `deployable=False`, state the measured
  evidence-backed reason as its own paragraph — mirror the existing entries'
  honesty (e.g. "classic Wilder RSI(14) 30/70 mean-reversion on SPY, net of
  realistic transaction costs, caps out around Sharpe 0.15 across every
  construction tried — a genuinely weak edge, not a fixable
  variant-selection artifact").
- Link the introducing PR.

## 5. Quick reference: where things live

| What | Where |
|---|---|
| Gate thresholds (single source of truth) | `validation/thresholds.py` |
| `deployable` property logic | `validation/harness.py:262` (`ValidationReport.deployable`) |
| Options-selling stress gate | `validation/stress_scenarios.py` (+ `validation/harness.py:191`'s `is_options_selling`/`stress_returns_fn`) |
| `STRATEGY_REGISTRY` (adapter, turnover, universe per strategy) | `scripts/refresh_validations.py:2207` |
| Cost model (mandatory, no static assumptions) | `execution/cost_model.py::TieredCostModel` |
| CLI entry point (real workflow) | `python -m scripts.refresh_validations --strategies <name> ...` |
| Fix-log rollup + rule statement | `docs/VALIDATION_STRATEGY_FIX_LOG.md` |
| Per-strategy doc template | `docs/signals/rsi_extremes.md`'s `## Backtest Validation` section |
| `Pilot.validation_strategy_id` join | `pilots/catalog.py` (see `new-signal-module` skill §6) |

# Implementation Plan: options_sor.py / lob_simulator.py audit fixes

Branch: `fix-sor-lob-simulator-audit-findings`

## Context

An operator-supplied audit of `pilots/options_sor.py` and
`pilots/lob_simulator.py` found real defects sitting alongside otherwise
correct Gillespie SSA / price-time-priority / Cont-Stoikov-Talreja (2010)
machinery. This plan covers the audit's prioritized subset:

- **#1 (HIGH, live-reachable today)** — `options_sor.py`'s hung-leg-hazard
  formula uses a signed (not absolute-value) delta as a stochastic scale
  parameter, so a PUT active leg's hung-leg probability silently collapses to
  its floor (0.02) instead of reflecting real hazard, while an economically
  identical CALL active leg reports the correct (much higher) value. Feeds
  the live `POST /pilots/options/sor/analyze` endpoint's `hung_prob < 0.35`
  policy gate.
- **#2 (MODERATE-HIGH, latent landmine)** — `lob_simulator.py`'s no-depth-data
  `mu_cancel` fallback returns a raw events/sec rate where every downstream
  caller expects events/(sec·share) — catastrophic the moment it's fed a
  nontrivial `queue_ahead`. `compute_lob_arrival_rates()` is public/exported/
  tested; any future caller inherits this otherwise.
- **#3 (MODERATE, same bug family as #2)** — the primary (depth-observed)
  `mu_cancel` formula divides by cancel event *count* instead of canceled
  *volume*. Folded in with #2 for internal consistency.
- **#4 (Fabrication-risk framing, live-reachable today)** — the module
  docstring claims empirically-calibrated Poisson rates, but the live
  endpoint (`simulate_queue_fill`) never calls `compute_lob_arrival_rates()`
  (zero production callers, confirmed by grep) — runs on fixed constants
  instead. Fixed via documentation correction + a `docs/known_issues/`
  write-up, not live-calibration wiring — see "Item #4 scope decision" below.

Items #5 (dual fill-probability formulas) and #6 (no commission model in SOR)
are explicitly deferred to a follow-up per the audit's own prioritization.

## Item #4 scope decision

Investigated whether `compute_lob_arrival_rates()` can be wired into the live
`simulate_queue_fill` path this pass. Confirmed genuinely not possible
without fabricating a new proxy:
- No real per-order LIMIT/CANCEL/MARKET event stream (L2/L3 order-flow data)
  exists anywhere in this codebase's data layer (grepped
  `data/market_data.py`, `data/fmp_client.py` — confirmed absent).
- CLAUDE.md's own Phase 32 circuit-breaker section already documents, as a
  settled finding: no configured market-data provider populates bid/ask size
  anywhere in this codebase's `Quote` type — confirmed by direct research,
  not an oversight.
- A synthetic order-flow proxy derived from bars (unlike VPIN's defensible
  bar-level BVC formula) would risk creating a NEW fabrication bug rather
  than fixing one.

Matches the audit's own offered fallback: correct the docstring's claim and
file a documented, disclosed gap.

## Files touched

- `pilots/options_sor.py` — Fix #1 (`abs()` around `active_leg["delta"]`).
- `pilots/lob_simulator.py` — Fix #2/#3 (`mu_cancel` formula) + Fix #4
  (docstring correction).
- `tests/test_options_sor.py` — new regression test for Fix #1.
- `tests/test_lob_simulator.py` — two new regression tests for Fix #2/#3.
- `docs/architecture/execution.md` — updated `options_sor.py`/`lob_simulator.py`
  entries describing the fixes and the #4 disclosure.
- `docs/known_issues/lob_simulator_uncalibrated_live_arrival_rates.md` — new.
- `docs/known_issues/README.md` — new index row.
- `.claude/sor_lob_audit_fixes_*` — this plan, task tracker, walkthrough.

## Verification

```bash
python3 -m pytest tests/test_options_sor.py tests/test_lob_simulator.py -q
```
All existing + new tests pass. Each new test independently confirmed to FAIL
against the pre-fix code (verified by temporarily reverting each fix and
re-running just that test) and PASS against the fix.

## Deferred follow-up (not in this PR)

- **#5** — `evaluate_optimal_queue_level` calls the less-rigorous
  `calculate_cont_stoikov_fill_probability` heuristic instead of the exact
  `compute_cst_fill_probability`; `p_reach`'s magic constant `1.5` is
  uncited.
- **#6** — `options_sor.py` has no commission/fee model; `policies_comparison`
  compares routes gross of cost, not reusing `execution/cost_model.py::TieredCostModel`.

Flagged via `spawn_task` at the end of this session rather than silently
dropped.

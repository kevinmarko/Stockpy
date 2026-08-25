# Secondary Audit Items — Walkthrough

## What was asked

"Let's look into these" secondary/lower-EV audit items: LLM commentary generation,
sector rotation/correlation engine, GEX module depth, and the multi-broker
gateway's NBBO logic.

## What was found and fixed

### 1. GEX 100x dollar-scaling bug (`pilots/options_gex.py`)

Every displayed dollar GEX figure was missing an industry-standard `×0.01`
normalization the module's own `dealer_hedging_flow` field already applied —
overstating the primary KPI/chart values by exactly 100x (one ATM SPY strike
computed to $3.46B). Fixed via a shared `PERCENT_MOVE_SCALING_FACTOR` constant
applied once at aggregation, removing the now-redundant re-application in
`get_options_gex_profile`. Zero-gamma-flip root-finding is unaffected
(scale-invariant).

Also in the same module: missing/zero/NaN IV and missing/unparseable expiration
previously fabricated a plausible `σ=0.25`/`dte=30.0` instead of excluding the
contract — a real, live-reachable CONSTRAINT #4 violation (yfinance routinely
reports `impliedVolatility=0.0` for stale quotes). Fixed by excluding the contract
and logging an aggregate warning.

`GexProfileView.tsx` never read the backend's own honest `chain_source`/
`spot_price_source` fields — a fully synthetic fallback chain rendered
indistinguishably from real market structure. Added an honesty banner mirroring
this codebase's `DemoDataBadge` pattern.

Not fixed (disclosed): a second, independent Black-Scholes gamma implementation
(verified numerically identical to the canonical `pilots/options_risk.py` today,
but a real drift risk per this repo's policy) — deferred because delegating would
add per-call price/delta/theta/vega/rho overhead to a hot root-finding loop that
only needs gamma.

### 2. SEC Rule 606 fabricated-zero (NOT `multi_broker_gateway.py` — the file initially in scope)

`execution/multi_broker_gateway.py` turned out to contain zero NBBO/price-
improvement logic at all. The real bug: `data/execution_audit_store.py`'s
`price_improvement` defaulted to `0.0` identically for "measured, zero
improvement" and "unmeasurable" (production NBBO coverage is structurally ~0% —
nothing populates `nbbo_bid`/`nbbo_ask`), making the live SEC 606 compliance
report's price-improvement rate/dollars structurally always ~0%/`$0.00`,
regardless of actual execution quality.

Fixed via a new additive `nbbo_available` column distinguishing the two cases, and
coverage-aware rate denominators (`nbbo_coverage_pct` surfaced everywhere a rate
is reported) in `execution/sec_rule_606_reporter.py` — dollar sums were already
correct, only the rates were fabricated-looking. A related dead-code bug
(`classify_limit_order`, tested but zero write-path callers) was wired in. A
defensive crossed-market guard was added to `execution/fix_gateway.py`'s
(currently fully-simulated) NBBO synthesizer, extracted into a standalone pure
function specifically so the guard is unit-testable.

Disclosed, not fixed: no real NBBO source is wired into production yet — doing so
would add a synchronous network call to the post-fill audit hot path, a design
decision deferred to its own follow-up.

### 3. Sector-selection similarity-term lookahead gap

`sector_selection_engine.py`'s semantic-similarity term had zero point-in-time
awareness — always embedded a target's CURRENT business description regardless of
what `as_of` date a backtest was scoring, defeating the lookahead-safety design
the sibling Sector Heat Factor term already had. Dormant (no backtest/replay
caller exists yet) but completely untested.

Fixed via a new `HistoricalStore.get_fundamentals_raw_json_asof` point-in-time
lookup mirroring the existing `get_fundamentals_asof` convention. Importantly:
I did NOT just assume this fix was safe for the live daily pipeline — I traced
every real fundamentals provider's actual return shape
(`data/yahoo_fundamentals.py::compute_fundamentals` never includes
`longBusinessSummary` at all; `YFinanceProvider`'s raw `.info` passthrough
includes it alongside a real report-date field from the same dict) and wrote a
regression test reproducing the one realistic degrade case (a payload with
`longBusinessSummary` but no report-date field) to confirm the fix fails closed
correctly rather than silently breaking sector selection in production.

A second, unrelated bug (`degraded_reason`'s operand order letting a routine
heat-side flag mask the real blocking similarity-side reason) was fixed in the
same pass.

### 4. LLM prompt injection via undelimited headlines

`llm/research.py` interpolated real, externally-sourced news headlines directly
into the LLM prompt with no delimiter separating untrusted data from
instructions. Bounded risk (the four Tier 9 schemas have zero numeric/action
fields and nothing downstream reads their output — confirmed by grep across
`sizing/`/`signals/`/`execution/`) but a real social-engineering risk to a human
operator reading the generated prose next to a real trading recommendation.

Fixed via explicit `<headline>`/`<research_context>` fencing (in both
`llm/research.py` and `llm/commentary.py`) plus a matching system-prompt
instruction to never follow instructions found inside them, with a sanitizer
neutralizing embedded angle-brackets/newlines that could otherwise forge a fake
closing delimiter. A trivial, unrelated cleanup (a leftover
`traceback.print_exc()` debug statement) was fixed in `llm/chart_insight.py` in
the same pass.

## Verification

- 405 tests across every directly-touched module (all new + pre-existing).
- Full offline suite: 12,358 passed. One pre-existing, unrelated failure
  (`test_settings_liveness.py`) confirmed via git history to predate this
  branch's changes — flagged separately, not folded into this fix.
- Narrow CI ruff gate + webapp typecheck + vitest: all clean.

## What's still open (disclosed, not silently accepted)

- Second Black-Scholes gamma implementation in `options_gex.py` (drift risk, not
  a live bug).
- No real NBBO source wired into `execution/order_manager.py`'s audit recording.
- No technical validation that LLM-generated prose numeric claims match real
  input data (policy-only, pre-existing design tradeoff).
- Pre-existing `docs/settings_liveness.json` staleness, unrelated to this branch,
  flagged via a separate background task.

# Secondary Audit Items — Implementation Plan

## Scope

Four previously-unaudited/partially-audited areas, flagged as lower-EV/secondary
follow-ups to an earlier, larger audit series:

1. LLM commentary generation (`llm/commentary.py`, `llm/chart_insight.py`,
   `llm/research.py`) — never audited; look for prompt-injection/hallucination-
   framing issues.
2. Sector rotation/correlation engine (`sector_selection_engine.py`) — ranking
   math not deep-audited (only confirmed semantic-embedding, not price-correlation,
   at a prior shallow pass).
3. GEX module (`pilots/options_gex.py`) — partially covered (dealer-gamma sign
   convention already confirmed correct); full pass needed on everything else.
4. Multi-broker gateway (`execution/multi_broker_gateway.py`) — NBBO/price-
   improvement math specifically, distinct from the already-audited
   `data/execution_audit_store.py`.

## Approach

1. Load `stockpy-quant-integrity` skill for the CONSTRAINT #4/#6/single-source-of-
   truth framework this repo's audits are graded against.
2. Dispatch 4 parallel, read-only audit agents (one per area), each instructed to:
   trace the real production call path, verify claims by reading/running code
   rather than assuming, and report CONFIRMED vs PLAUSIBLE findings with concrete
   failure scenarios.
3. Synthesize findings, verify the highest-severity ones myself before touching
   code, then fix genuine bugs directly (this branch is already off `main`, so no
   separate branch-creation step needed).
4. For each fix: implement, add/extend regression tests, run the targeted test
   suite, and write a `docs/known_issues/*.md` write-up per this repo's established
   convention.
5. Update `CLAUDE.md`/`AGENTS.md` (auto-synced) and the relevant
   `docs/architecture/*.md`/`docs/signals/*.md` entries.
6. Run the full offline test suite + the narrow CI ruff gate + webapp typecheck as
   a final verification pass.

## Documentation-update step (scoped up front, per CLAUDE.md's Implementation Plan requirement)

- `CLAUDE.md`/`AGENTS.md`: one consolidated bullet summarizing all four fixes.
- `docs/architecture/execution.md`: update the `pilots/options_gex.py`,
  `execution/order_manager.py`, `execution/sec_rule_606_reporter.py`,
  `data/execution_audit_store.py`, and `execution/fix_gateway.py` entries.
- `docs/signals/sector_selection.md`: correct stale "ships in a follow-on PR"
  language (the similarity term and `data/sector_descriptions.yaml` already exist)
  and document the lookahead fix.
- `docs/known_issues/README.md` + 4 new dated write-ups, one per finding area.
- No dedicated `docs/architecture/` section exists for `llm/commentary.py`/
  `llm/research.py`/`llm/chart_insight.py` (they're referenced as consumers
  elsewhere, not documented as their own subsystem) — covered via the new
  known-issues doc and the CLAUDE.md bullet instead of inventing a new
  architecture-doc section for a defense-in-depth prompt-fencing change.

## Findings and disposition

| # | Area | Finding | Severity | Disposition |
|---|------|---------|----------|-------------|
| 1 | GEX | `net_gex`/`call_gex`/`put_gex` missing industry-standard `×0.01` "per 1% move" factor — 100x overstated, internally inconsistent with the module's own `dealer_hedging_flow` field | High | **Fixed** |
| 2 | GEX | Missing/zero/NaN IV and missing/unparseable expiration fabricated `σ=0.25`/`dte=30.0` instead of excluding the contract (CONSTRAINT #4) | Medium | **Fixed** |
| 3 | GEX | `GexProfileView.tsx` never read the backend's own `chain_source`/`spot_price_source` honesty fields | High | **Fixed** |
| 4 | GEX | Reimplements Black-Scholes gamma instead of `pilots/options_risk.py` | Low (drift risk, verified numerically identical today) | Disclosed, not consolidated (perf tradeoff in a hot root-finding loop — a real design decision, deferred) |
| 5 | NBBO/606 | `execution/multi_broker_gateway.py` has zero NBBO/price-improvement logic — real logic lives in `data/execution_audit_store.py`/`execution/sec_rule_606_reporter.py` | — | Corrected the audit's own scope; real bug found there instead |
| 6 | NBBO/606 | `price_improvement` defaulted to `0.0` identically for "measured zero" and "unmeasurable" (production NBBO coverage ~0%) — SEC 606 report structurally always ~0%/`$0.00` | High | **Fixed** (new `nbbo_available` column + coverage-aware rates) |
| 7 | NBBO/606 | `classify_limit_order` unit-tested, zero write-path callers — "Marketable Limit" structurally unreachable | Medium | **Fixed** (wired in, gated on real `limit_price`+NBBO) |
| 8 | NBBO/606 | `synthesize_nbbo` has no crossed-market guard (unreachable today, real insurance for future external-quote input) | Low | **Fixed** (extracted to a testable pure function + guard) |
| 9 | Sector | Semantic-similarity term has no point-in-time awareness — defeats the heat term's already-causal design; dormant (no backtest caller yet), zero test coverage | High (latent) | **Fixed** (new `get_fundamentals_raw_json_asof` PIT lookup) |
| 10 | Sector | `degraded_reason = heat_degraded_reason or similarity_reason` — operand order let a routine heat flag mask the real blocking similarity reason | Medium | **Fixed** (swapped operand order) |
| 11 | LLM | Headlines interpolated into the Opal research prompt with no delimiter between untrusted data and instructions | Medium (bounded — no numeric/action fields, nothing downstream reads LLM output) | **Fixed** (`<headline>`/`<research_context>` fencing + system-prompt instruction) |
| 12 | LLM | No technical validation that LLM-asserted numeric claims match real data | Low (disclosed design tradeoff) | Disclosed, not attempted (post-hoc fact-checking of free text is a separate, larger effort) |
| 13 | LLM | Leftover `traceback.print_exc()` debug statement in `llm/chart_insight.py` | Trivial | **Fixed** |

## Verification performed

- Targeted test suites for every touched module: 405 passed (Python).
- Full offline suite (`pytest -m "not network"`): 12,358 passed, 24 skipped, 88
  deselected, 1 pre-existing unrelated failure (`test_settings_liveness.py`,
  confirmed stale before this branch's changes — flagged separately via
  `spawn_task`, not fixed here).
- Narrow CI ruff gate (`--select=F821,F822,F823,E9`) on every touched file: clean.
- `npm run typecheck` (webapp): clean.
- `vitest run` on the touched webapp test file: 9 passed.

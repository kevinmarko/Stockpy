---
name: backtest-optimization
description: >-
  Run and interpret a strategy backtest/validation pass against this repo's
  deployability gate (PBO/DSR/Sharpe/MaxDD, plus the options-selling stress
  gate) via validation/harness.py. Use when asked to optimize a strategy's
  parameters, run or re-run a backtest, interpret PBO/DSR/Sharpe/MaxDD
  numbers, or fix a failing STRATEGY_REGISTRY deployability gate. This skill
  is deliberately a thin pointer -- it states the exact gate and commands,
  then hands off the deep diagnosis playbook (failure-mode-to-fix-lever
  mapping, the two-place documentation requirement) to the sibling
  strategy-validation skill rather than duplicating it.
---

<!--
  Ported from this repo's Claude Code sibling skill (`.claude/skills/backtest-optimization/SKILL.md`)
  to Antigravity's skill format. Frontmatter and body content are carried over verbatim --
  Antigravity's own `google-antigravity-sdk` skill and this repo's existing `.agents/skills/supabase`
  skill both use the same minimal `name` + `description` frontmatter shape Claude's SKILL.md already
  used here, so no restructuring was required for this port beyond this note.
-->

# Backtest Optimization

**Deliberate design choice: this skill points at `strategy-validation`
(`.agents/skills/strategy-validation/SKILL.md`) rather than re-deriving its
own copy of the diagnosis playbook.** That sibling skill already documents,
in 260 lines with exact line-number citations, the gate logic
(`validation/harness.py:262`), the CLI, the failure-mode → fix-lever mapping
distilled from the real 2026-07 fix series (`docs/VALIDATION_STRATEGY_FIX_LOG.md`),
and the mandatory two-place documentation step. Forking that into a second,
independently-drifting copy here would violate this repo's own DRY
convention and would go stale the next time the gate or the fix log changes.
Use this skill as the fast-reference entry point; read `strategy-validation`
in full before doing the actual optimization/fix work.

## The gate, in one table

Source of truth: `validation/thresholds.py`. Never hard-code these numbers
elsewhere, and never loosen them to force a pass (see
`docs/VALIDATION_STRATEGY_FIX_LOG.md`'s header rule, which `strategy-validation`
§4b quotes in full).

| Metric | Threshold |
|---|---|
| PBO (Probability of Backtest Overfitting) | `< 0.50` |
| DSR (Deflated Sharpe Ratio) | `> 0.95` |
| Net-of-cost Sharpe | `> 0.50` |
| Max Drawdown | `< 30%` |

`ValidationReport.deployable` is `True` iff all four pass (plus the stress
gate below, when applicable) — see `strategy-validation` §1 for the exact
boolean composition and NaN fail-closed behavior.

**Options-selling addendum**: any premium-selling strategy (Put Credit
Spreads, Iron Condors, ...) needs a 5th gate, constructed via
`is_options_selling=True` and a `stress_returns_fn(start, end) -> pd.Series`
passed to the harness (this is a **constructor parameter**, not a CLI flag —
`validation/harness.py`'s own `main()` argparse has no `--is-options`/
`--is-options-selling` switch; only `scripts/refresh_validations.py`'s
adapter-backed path wires it per strategy). Replayed across four dated shock
windows in `validation/stress_scenarios.py` (`OCT_2008`, `FEB_2018`,
`MAR_2020`, `AUG_2024`). Deployable requires max drawdown `< 50%` during
stress AND account survival in **every** window — fails closed (never
deployable) if `stress_returns_fn` is omitted for an options-selling
strategy. See `strategy-validation` §1 for the exact gate wiring.

## Exact commands

```bash
# Sanity-check the harness mechanics in isolation (hardcoded buy-and-hold-SPY
# strategy_fn -- NOT a STRATEGY_REGISTRY lookup; --strategy here is a report
# label only):
python3 -m validation.harness --strategy <name> --start YYYY-MM-DD --end YYYY-MM-DD

# The REAL, adapter-backed workflow for an actual registered strategy:
python -m scripts.refresh_validations --strategies <name> \
  --start 2005-01-01 --end 2026-08-01

# Re-run the entire STRATEGY_REGISTRY fleet, machine-readable summary
# (exit code 1 if any strategy errored or failed to deploy):
python scripts/refresh_validations.py --json
```

See `strategy-validation` §2 for the full flag reference
(`--n-cpcv-splits`/`--n-test-splits`, `--output-dir`) and why the two
commands above answer genuinely different questions.

## When you're actually optimizing parameters

"Optimize a strategy's parameters" in this repo does **not** mean grid-search
the gate metrics until something clears threshold — CLAUDE.md is explicit
that thresholds are never loosened and filters are never date-snooped to a
specific crash window. Two other repo-wide constraints bind here too:

- Any new trading rule must be optimized in `vectorbt` and validated in
  `backtrader` before landing in `strategy_engine.py` (CLAUDE.md's
  "Conventions enforced in this codebase").
- Backtests must use `execution.cost_model.TieredCostModel` for
  commission/slippage — never a static cost assumption.

For the actual failure-mode → fix-lever playbook (which gate is failing,
what the proven causal fix is, and when an honest `deployable=False` is the
correct outcome rather than a failure to hide), read `strategy-validation`
§3 in full rather than guessing — it cites the specific 2026-07 worked
examples (Faber SMA-200 trend gate, empirically-measured turnover
correction, variant-count reduction) this repo has already proven out.

## Mandatory documentation (two places, every time)

Whether the outcome is a successful fix or an honest, evidence-backed
`deployable=False`, both are required — see `strategy-validation` §4 for the
exact template to follow:

1. A `## Backtest Validation` section in `docs/signals/<name>.md` (before/after
   PBO/DSR/Sharpe/MaxDD table, the causal lever used, and for a `False`
   verdict, the measured evidence-backed reason).
2. A dated entry appended (never rewritten) to
   `docs/VALIDATION_STRATEGY_FIX_LOG.md`.

Skipping either for an honest failure is the one mistake this rule exists to
prevent.

## Quick reference

| What | Where |
|---|---|
| Gate thresholds | `validation/thresholds.py` |
| `deployable` property | `validation/harness.py:262` |
| Options-selling stress gate | `validation/stress_scenarios.py` |
| `STRATEGY_REGISTRY` | `scripts/refresh_validations.py:2207` |
| Full diagnosis playbook + doc template | `.agents/skills/strategy-validation/SKILL.md` |

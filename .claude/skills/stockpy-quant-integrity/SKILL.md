---
name: stockpy-quant-integrity
description: >-
  Load this whenever writing, reviewing, auditing, or merging a change in the
  Stockpy/InvestYo repo that touches pilots/, sizing/, signals/, execution/,
  or validation/ — including PR review, "is this safe to merge" judgment
  calls, writing a walkthrough for your own work, or deciding whether a
  strategy/tool is ready to expose to the Autopilot. Also trigger any time
  you're about to report, compute, or trust a quant metric in this repo
  (Sharpe, DSR, PBO, MaxDD, Kelly Target, VaR/ES, win rate) or write text
  claiming a check passed, a gate is enforced, or a value was measured.
  This is the distilled reference for CONSTRAINT #4 (never fabricate a
  metric), CONSTRAINT #6 (fail closed), the deployability gate, and this
  repo's single-source-of-truth rules — use it BEFORE claiming something is
  done, not after, and especially when you're the one who wrote the code
  you're now describing as tested and correct.
---

# Stockpy Quant Integrity

## Why this exists

This repo has a real, documented history of a builder agent's own completion
report being wrong — not lying, just wrong, in the specific way that happens
when a change looks locally correct and nobody independently re-checked it
against the actual data or the actual running code. Confirmed instances
already found by audit passes, not by the original author:

- Two divergent Kelly-sizing formulas lived at different call sites for a
  while before someone noticed they disagreed.
- A live API endpoint (`sizing/hrp_cvar_optimizer.py`'s CVaR field) shipped
  hardcoded `"cvar_95": float(0.05), # placeholder` instead of the real
  computed value — the module's own math was correct, only the wiring
  fabricated the number.
- `pilots/zero_dte_engine.py`'s mandatory 15:45 ET hard-exit gate is fully
  implemented and unit-tested, and is simply never called from any
  production path — the UI shows "15:45 ET Auto-Close" as if it's live.
- Five live options-selling pilots (`earnings_crush`, `vol_mispricing`,
  `dispersion_trading`, `zero_dte_engine`, `gamma_scalper`) plus
  `copula_stat_arb` submit real paper trades with zero `STRATEGY_REGISTRY`
  entry, zero validation-log entry, and zero stress-scenario coverage.

None of these were carelessness — they're the natural failure mode of a
codebase this large, where "I wrote it and the happy path works" quietly
substitutes for "I verified it against the actual gate." This skill exists
so that substitution stops happening by default.

## Quick checklist — work through this before saying something is done

1. Did I actually re-run the check, or am I inferring it passed?
2. Does every number in what I'm about to report come from a real
   computation, or could any of it be a placeholder, a fallback, or an
   assumption dressed up as a measurement?
3. If I'm touching sizing, pricing, Greeks, or win-rate/payoff logic — does
   this already exist somewhere in `sizing/`, `pilots/`, or `signals/`? (It
   almost certainly does. Find it and call it before writing new math.)
4. If this change could affect what an Autopilot actually trades — has the
   underlying strategy cleared the deployability gate, or does my own
   response say plainly that it hasn't?
5. Am I the only one who has looked at this, and does that matter here?

If the honest answer to any of these is "I'm not sure" or "I didn't check,"
that's the finding — say so, don't round it up to "looks good."

---

## The four rules

### CONSTRAINT #4 — never fabricate a metric

Missing, unavailable, or uncomputable data returns `NaN`, `None`, or an
explicit "insufficient data" / "unavailable" status — never a zero, a
plausible-looking placeholder, or a silently reused stale value standing in
for a real one. The reasoning: a fabricated number is worse than a missing
one, because a missing number is visibly missing and a fabricated one looks
exactly like a real measurement until someone loses money trusting it. This
applies to code you write (a function's return value) and to text you write
(a sentence claiming "X was verified" when you didn't verify X).

Watch especially for: hardcoded literals standing in for something that
should be a live computation or a `settings.*` value; a `try/except` that
swallows an error and returns a friendly default instead of `None`/`NaN`; a
cached or fallback value getting presented as fresh; a strategy's backtest
number being asserted without having actually re-run
`validation/harness.py` or the relevant `investyo` MCP tool
(`run_validation_harness`, `get_var_es_metrics`, `run_backtest`) against
current data.

### CONSTRAINT #6 — fail closed

A failure anywhere in the pipeline (network error, missing history, bad
input, an exception in a third-party library) must never crash the whole
run and must never silently relax a risk limit or a gate to keep going. The
reasoning: in a trading system, "the safe thing broke, so let's just skip
it and continue" is exactly backwards — a broken safety check should make
the system more conservative, not less. Concretely: a coverage gap should
never *loosen* a portfolio cap (it should exclude the affected name, not
count it as zero risk); a failed HMM regime detector should degrade to
neutral, not to "risk on"; a missing options chain should degrade the whole
directive to `Cash/Wait`, not to a directive computed on partial data.

### Single source of truth — don't reimplement quant math that already exists

Before writing new logic for position sizing, Greeks, IV/vol surface,
cointegration, or credibility scoring, assume it already exists in this
codebase and go find it:

| Domain | The one real implementation | Never do this |
|---|---|---|
| Kelly / position sizing | `sizing/kelly.py`, `sizing/vol_target.py`, `sizing/position_sizer.py::size_position()`, `StrategyEngine._calculate_kelly_sizing()` | A new win-rate/payoff/Kelly-fraction formula anywhere else |
| Options Greeks | `pilots/options_risk.py` | A second Black-Scholes Greeks calculation |
| IV surface / VRP | `pilots/volatility_surface.py`, `technical_options_engine.py::build_premium_directive` | A new IV-rank or VRP formula |
| Pairs cointegration | `pairs/cointegration.py`, `pairs/kalman_hedge.py` | A new ADF/hedge-ratio implementation |
| News/source credibility | `signals/credibility.py`, `signals/news_catalyst.py` | A new sentiment-scoring pass over raw headlines |

The reasoning isn't just DRY — it's that this repo has already paid the
cost of two implementations silently drifting apart once (the Kelly bug
above), and the fix was consolidating to one call site everyone uses. Adding
a second one anywhere re-creates the exact bug that was already found and
fixed.

### The deployability gate

Before treating any strategy's numbers as trustworthy enough to inform an
Autopilot decision — not just "the backtest ran," but "the backtest ran and
passed" — check all of:

- **PBO < 0.5** (Probability of Backtest Overfitting)
- **DSR > 0.95** (Deflated Sharpe Ratio)
- **Net-of-cost Sharpe > 0.5**
- **Max Drawdown < 30%**
- **Stress-scenario gate**, for anything options-selling — must survive all
  four canonical shock windows (`OCT_2008`, `FEB_2018`, `MAR_2020`,
  `AUG_2024`) in `validation/stress_scenarios.py`

And — this is the part that's easy to skip — confirm the strategy actually
has a `STRATEGY_REGISTRY` entry and a `docs/VALIDATION_STRATEGY_FIX_LOG.md`
entry at all. A strategy with no registry entry hasn't failed the gate, it
has never been run through it, which is a different and in some ways worse
state: nothing is stopping it from executing real trades while carrying no
verified numbers whatsoever. If you find one of these (the five options
pilots above are already confirmed examples), say so explicitly rather than
treating "no registry entry" as a minor gap to mention in passing.

---

## Before you call it done — the verification checklist

Work through this literally, in a fresh read of the actual diff and a fresh
run of the actual commands — not from memory of what you intended to do:

- [ ] **Re-ran the targeted tests myself**, this session, on this diff —
  not inferred from an earlier pass or from the plan saying tests would be
  written.
- [ ] **Grepped for hardcoded numeric literals** that should be `NaN`,
  `settings.X`, or `validation.thresholds` instead.
- [ ] **Grepped for a second implementation** of anything in the
  single-source-of-truth table above.
- [ ] **Confirmed no new "read-only" analytics path is actually reachable
  from order submission** — search for `POST .../execute`,
  `submit_order`, `place_order`, or a broker call anywhere downstream of
  the change.
- [ ] **Checked `STRATEGY_REGISTRY` / validation-fix-log / stress-scenario
  status** for any pilot module the change touches or depends on, and
  stated that status plainly rather than assuming "existing code" means
  "already covered."
- [ ] **Confirmed the documentation-update step happened** — which of
  `CLAUDE.md`/`AGENTS.md`, `docs/architecture/*.md`, or `docs/signals/*.md`
  needed a change, and did that change actually get made, not just
  identified.
- [ ] **Confirmed PR artifact naming**: `.claude/<slug>_implementation_plan.md`
  / `_task.md` / `_walkthrough.md`, uniquely scoped to this task/branch —
  never a bare `plan.md`/`walkthrough.md`.

## When your own read isn't enough

Claude Code has a blocking `Stop` hook
(`.claude/hooks/verify_before_stop.sh`) that refuses to end a session while
a targeted test is failing. Antigravity's equivalent
(`.agents/hooks/stop_test.sh`) is advisory-only — it can surface a failure
but cannot block. If you're working in Antigravity, that means "don't
mark this done until tests pass" is enforced by you actually doing it, not
by the tooling catching you if you don't. Say so if you're not certain a
check has genuinely been re-run, and treat a second, independent pass (a
fresh Claude Code session on a clean pull, ideally) as the real gate for
anything touching `pilots/`, `sizing/`, `signals/`, `execution/`, or
`validation/` — not a nice-to-have on top of your own review.

## Currently open, already-flagged gaps

Don't rediscover these from scratch, and don't assume they're fixed just
because they aren't mentioned in whatever task you're on:

- **Is the 0DTE live-exit gate real?** `manage_0dte_exits()` IS actively wired in `desktop/daemon_runtime.py:634-640` and conditioned on `settings.OPTIONS_0DTE_ENABLED`. Do not claim it is dead code or unconnected.
- **Is the strategy registry honest?** Don't hallucinate that a strategy is registered if it isn't. The 5 strategies `earnings_crush`, `dispersion_trading`, `zero_dte_engine`, `gamma_scalper`, and `copula_stat_arb` have zero `STRATEGY_REGISTRY` entries. However, `vol_mispricing` IS explicitly registered in `scripts/refresh_validations.py:3496` with a measured fail. Know the difference between unregistered and registered-but-failing.

If a task touches any of these modules and doesn't mention fixing the gap,
flag it in your response rather than silently working around it.

## Where to go for the full picture

This skill is a distilled checklist, not a replacement for the real
architecture docs — it deliberately doesn't repeat everything in them. Read
the relevant one via the `investyo` MCP's `get_doc` tool (or the repo
directly) before touching a domain for the first time:

- `docs/architecture/signal-engines.md` — sizing/, forecasting, regime
- `docs/architecture/execution.md` — broker, risk gate, order queue, options
  pilots
- `docs/architecture/validation-and-signals.md` — validation harness,
  signals/ package, pairs
- `docs/architecture/observability-and-apis.md` — `investyo_mcp_server.py`
  and the other API surfaces
- `CLAUDE.md` — branch workflow, PR artifact rules, the full constraint
  numbering this skill draws from

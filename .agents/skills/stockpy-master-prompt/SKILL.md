---
name: stockpy-master-prompt
description: >-
  Master session prompt, startup ritual, and operating constraints for Stockpy
  (InvestYo). Use at the start of any new agent session (Claude Code, Cursor,
  Jules, Gemini Antigravity, or fresh chat) before starting work in this repo.
  Establishes the 5 non-negotiable constraints (advisory-only, never fabricate a
  metric / CONSTRAINT #4, fail closed / CONSTRAINT #6, single source of quant
  truth, deployability gate), the startup ritual, output format requirements,
  completion checklist, agent-specific enforcement notes, and known open gaps.
---

# Stockpy / InvestYo — Master Session Prompt

Paste or load this skill at the start of any new agent session (Claude Code,
Cursor, Jules, Gemini Antigravity, or a fresh chat) before starting work on this
repo. It exists to make every agent behave like it already read the audit
history, even if it didn't.

---

## 1. Who you are working for and on what

You are working in **Stockpy** (also called **InvestYo**) — a solo-developer,
personal quantitative trading *advisory* platform, hosted on a Mac mini.

- Backend: Python/FastAPI, ~28 modules
- Storage: SQLite via `PaperAccountStore`
- Frontend: React/TypeScript PWA
- Market data: FMP (primary), yfinance (fallback)
- Execution: **manual only**, via Robinhood — nothing in this system places
  live trades automatically
- MCP integration: `investyo` server, both a local registration
  (`investyo-platform`, always current) and a GCP-hosted one (`investyo`,
  drifts on redeploy — check the serving commit SHA header before trusting
  any `get_doc` response from it)

Governing philosophy: **audit-first**. Fix core pipeline issues before
building new features on top of them. A silent failure is a high-priority
bug, not a minor one — this repo has a documented history of "looks correct,
nobody re-checked it" bugs shipping to a live paper-trading account.

## 2. Non-negotiable constraints, in priority order

If two of these ever pull in different directions, the higher-numbered one
loses.

### 1 — Advisory-only is absolute

Nothing in this system may submit a live order. Autopilot generates
BUY/SELL/HOLD signals; a human places every trade manually. Any surface that
*could* place an order — a new UI action button, an MCP tool
(`place_equity_order` and friends), agent-generated code that calls a broker
API — must explicitly state whether it's a review ticket or a live order
**before** it gets implemented, not after. The standing cautionary reference
is Knight Capital's $440M loss from unreviewed live-trading code. If a task
touches anything execution-adjacent and doesn't already make this
distinction explicit, stop and flag it rather than assuming "it's probably
just advisory."

### 2 — Never fabricate a metric (CONSTRAINT #4)

Missing, unavailable, or uncomputable data returns `NaN`, `None`, or an
explicit "insufficient data" status — **never** a zero, a placeholder that
looks plausible, or a silently reused stale value. This applies to code
(a function's return value) and to text (a sentence claiming something was
verified when it wasn't). A fabricated number is worse than a missing one:
missing is visibly missing; fabricated looks exactly like real data until
someone loses money trusting it.

Confirmed instance of this exact failure: a live endpoint shipped
`"cvar_95": float(0.05), # placeholder` instead of the real computed value —
correct math, fabricated wiring. Watch for the same pattern: hardcoded
literals standing in for a live computation, a `try/except` that swallows an
error and returns a friendly default instead of `None`/`NaN`, a cached value
presented as fresh, a backtest number asserted without actually re-running
the validation harness.

### 3 — Fail closed (CONSTRAINT #6)

A pipeline failure (network error, missing history, bad input, a third-party
exception) must never silently relax a risk limit to keep going. A coverage
gap excludes the affected symbol — it does not count it as zero risk. A
failed regime detector degrades to neutral, not to "risk on." A missing
options chain degrades the whole directive to `Cash/Wait`, not to a
directive computed on partial data.

### 4 — Single source of truth — find it before you rebuild it

Before writing new sizing, Greeks, IV/vol-surface, cointegration, or
credibility-scoring logic, assume it already exists and go find it:

| Domain | The one real implementation | Never do this |
|---|---|---|
| Kelly / position sizing | `sizing/kelly.py`, `sizing/vol_target.py`, `sizing/position_sizer.py::size_position()`, `StrategyEngine._calculate_kelly_sizing()` | A new win-rate/payoff/Kelly formula anywhere else |
| Options Greeks | `pilots/options_risk.py` | A second Black-Scholes calculation |
| IV surface / VRP | `pilots/volatility_surface.py`, `technical_options_engine.py::build_premium_directive` | A new IV-rank or VRP formula |
| Pairs cointegration | `pairs/cointegration.py`, `pairs/kalman_hedge.py` | A new ADF/hedge-ratio implementation |
| News/source credibility | `signals/credibility.py`, `signals/news_catalyst.py` | A new sentiment-scoring pass |

This repo already paid the cost of two Kelly implementations silently
drifting apart once. Adding a second implementation of anything above
re-creates that exact bug.

### 5 — The deployability gate

Before treating any strategy's numbers as trustworthy enough to inform an
Autopilot decision, confirm all of:

- **PBO < 0.5** (Probability of Backtest Overfitting)
- **DSR > 0.95** (Deflated Sharpe Ratio)
- **Net-of-cost Sharpe > 0.5**
- **Max Drawdown < 30%**
- **Stress-scenario gate** (options-selling strategies only) — survives all
  four canonical shock windows (`OCT_2008`, `FEB_2018`, `MAR_2020`,
  `AUG_2024`) in `validation/stress_scenarios.py`
- A `STRATEGY_REGISTRY` entry and a `docs/VALIDATION_STRATEGY_FIX_LOG.md`
  entry actually exist

No registry entry is not "hasn't failed the gate" — it's "never ran through
the gate at all," which means nothing is stopping it from executing real
paper trades while carrying zero verified numbers. Say that plainly if you
find it; don't fold it into a minor-gaps footnote.

## 3. Before you touch anything: the startup ritual

1. Read `memory_read /areas/stockpy.md` for architecture notes and the phase
   gate structure.
2. If the task touches `pilots/`, `sizing/`, `signals/`, `execution/`, or
   `validation/` — read `stockpy-quant-integrity` (`.claude/skills/stockpy-quant-integrity/SKILL.md` or `.agents/skills/stockpy-quant-integrity/SKILL.md`)
   in full before writing anything.
3. Inspect the **live code**, not a plan, doc, or your own memory of the
   repo. Use Desktop Commander (`start_process` with `grep`/`git log`/`git
   show` is more reliable than offset-guessing with `read_file`) or the
   `investyo` MCP tools. Plans generated by agents — including your own
   past output — frequently describe a state that no longer matches the
   actual repo.
4. If pulling docs from the `investyo` MCP server, check the serving commit
   SHA header first; the GCP-hosted registration drifts from `main` on
   redeploy, the local one doesn't.

## 4. Output format requirements

- Implementation plans: `.claude/<slug>_implementation_plan.md` with a
  companion `_task.md` tracker — never a bare `plan.md`. Every plan opens
  with a **§0 dependency check** confirming live schema, module interfaces,
  and feature flags before any code is written, and closes with AGENT
  HANDOFF NOTES plus a statement of which of `CLAUDE.md`/`AGENTS.md`/
  `docs/architecture/*.md` need updating — and whether that update actually
  happened, not just got identified.
- Code: complete, fully executable — no snippets, no pseudocode.
- Any refactor of financial math ships with parity tests against the old
  implementation.
- Infra steps: copy-pasteable terminal commands, not prose instructions.

## 5. Before calling anything "done"

Work through this against a fresh read of the actual diff and a fresh run of
the actual commands — not from memory of what you intended to do:

- [ ] Re-ran the targeted tests yourself, this session, on this diff.
- [ ] Grepped for hardcoded numeric literals that should be `NaN`,
      `settings.X`, or `validation.thresholds`.
- [ ] Grepped for a second implementation of anything in the single-source-
      of-truth table.
- [ ] Confirmed no "read-only" analytics path is actually reachable from
      order submission — search for `POST .../execute`, `submit_order`,
      `place_order`, or any broker call downstream of the change.
- [ ] Checked `STRATEGY_REGISTRY` / validation-fix-log / stress-scenario
      status for any pilot the change touches, and stated that status
      plainly.
- [ ] Confirmed the documentation-update step actually happened.
- [ ] Confirmed PR artifact naming follows the `.claude/<slug>_*` pattern.

If the honest answer to any check is "I'm not sure" or "I didn't check,"
that *is* the finding. Say so — don't round it up to "looks good."

## 6. Agent-specific enforcement notes

- **Claude Code**: has a blocking `Stop` hook
  (`.claude/hooks/verify_before_stop.sh`) that refuses to end a session
  while a targeted test is failing.
- **Gemini Antigravity**: the equivalent hook (`.agents/hooks/stop_test.sh`)
  is advisory-only — it can surface a failure but not block. If you're
  working in Antigravity, "don't mark this done until tests pass" is
  enforced by you doing it, not by tooling catching you if you don't.
- **Any agent's self-reported test results are untrusted input** to the
  next reviewer, including a fresh instance of yourself. Agents in this
  project have been observed auto-proceeding past human review gates and
  reporting suspiciously clean results on large volumes of new code. Treat
  a second, independent pass — a fresh session on a clean pull — as the
  real gate for anything touching the five directories above, not a
  nice-to-have.
- Worktree code (e.g. Gemini Antigravity's
  `~/.gemini/antigravity/worktrees/[repo]/[branch]/`) may be ahead of,
  behind, or inconsistent with `main`. Read raw test output before any
  merge decision.

## 7. Known open gaps — don't rediscover these, don't assume they're fixed

- `pilots/zero_dte_engine.py`'s mandatory 15:45 ET hard-exit gate is fully
  implemented and unit-tested but never called from any production path —
  the UI shows it as live.
- `earnings_crush`, `vol_mispricing`, `dispersion_trading`,
  `zero_dte_engine`, `gamma_scalper`, and `copula_stat_arb` submit real
  paper trades with zero `STRATEGY_REGISTRY` entries, zero validation-log
  entries, and zero stress-scenario coverage.
- Active trading universe (~430 symbols, heavy OTC/ADR/small-cap) is
  disconnected from the forecast/drift-tracking universe (a fixed 26-symbol
  semiconductor/mega-cap list) — `forecast_available` returns false for
  essentially every held position, and PIT fundamentals coverage sits
  around 7% of the active universe. This is flagged as a root cause behind
  several other pipeline gaps, not a symptom to patch locally.
- A Robinhood MCP live-order server (`place_equity_order` and similar
  tools) has been flagged as a direct conflict with the advisory-only
  design and should be gated or removed before further integration work —
  confirm its status before assuming it's inert.
- If a task touches any module above and doesn't mention fixing the
  relevant gap, flag it in your response instead of quietly working around
  it.

## 8. Your first move in this session

State which of the constraints in §2 are actually relevant to the task
you've been given, complete the §3 startup ritual, and only then start
writing code or a plan.

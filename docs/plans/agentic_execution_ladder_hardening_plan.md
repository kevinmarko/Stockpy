# Hardening Plan: Agentic Execution Ladder & Options-Scope Findings

## Context

An operator-supplied research note raised a set of concerns about the Agentic Trading tab's
execution-mode ladder, the simulation engine's optional dependencies, and whether options trading
should be extended into live execution. As with the Compass audit before it
(`docs/plans/COMPASS_AUDIT_HARDENING_PLAN.md`), the note was checked directly against the live repo
rather than acted on at face value — several of its claims are stale, backwards, or describe a
capability the operator has already declined. What remains after verification is a small set of
real, confirmed findings: one genuine UI bug in the execution ladder widget, one real hardening gap
in `simulation_engine.py`'s optional-dependency handling that has a concrete blast radius on the new
lean MCP-only VPS deploy, and one substantial, disclosed-but-unbuilt feature (a closed-loop paper
trading cycle). This plan hands those confirmed items to Antigravity/Gemini to implement — organized
by `CLAUDE.md`'s own risk tiers — with Claude Code auditing the finished work afterward against the
acceptance criteria defined here.

This plan supersedes several stale claims from the prior operator-supplied research note (see the
corrections table below) and reflects verified, current repo state as of **2026-08-29**.

**Everything not listed under "Confirmed gaps" below is either already resolved, already declined by
the operator, or genuinely out of scope — re-doing already-shipped work or reversing a recorded
decision would be wasted effort and risks regressions.**

---

## Corrections to prior claims

| Prior claim | Verified reality |
|---|---|
| `PUT /automation/execution-mode` "lacks test coverage and throws an unhandled error" | Stale. Fixed in PR #347 (2026-07-18, `fix(pilots-api): resolve PydanticUserError crash on PUT /automation/execution-mode`) and given ~20 dedicated tests in PR #611 (2026-08-05, `fix(settings): require typed confirmation before PUT /automation/execution-mode flips ADVISORY_ONLY/DRY_RUN`). `tests/test_pilots_api.py::TestExecutionModeWrite`/`TestExecutionModeConfirmation` (lines 3178 and 3374) cover it thoroughly today. No action needed. |
| "Deep-link... rather than a second, simplified copy" | Already done. `AgenticTrading.tsx`'s `ControlsSection` has no execution-mode toggle — it links to Settings (`docs/handovers/agentic_trading_synthesis.md:124`, tested in `AgenticTrading.test.tsx:385-389`). A different, real bug was found in the same area instead (see T3). |
| "Simulation step enhanced by installing optional deps vectorbt/backtrader" | Backwards. Both are already required (`requirements.txt`), not optional. The real defect is the opposite direction — see T4. |
| "Add options trading to the Live step" | Explicitly declined by the operator. Conflicts with 8 separately-recorded architecture decisions plus 6 hardcoded, regression-tested "Advisory-Only Mode: Live ... options order execution is disabled" refusals across `pilots/*.py`, plus the `robinhood-execution` skill's equities-only scope and `agentic-discovery`'s explicit ban on ever calling an option-order tool. Not planned. |
| SecProve Agent Safety Kit | Zero mentions anywhere in this repo. Unvetted third party. Out of scope for this pass. |
| "Closed-loop paper mode... run agentic-discovery -> advisory -> execute_paper_trade on a schedule" | Real and confirmed unbuilt — `docs/handovers/agentic_trading_synthesis.md:140-143` already names this as "(Future, not started)." This is the plan's one substantial new feature. |

---

## Low-risk tier (docs/tests only — may commit straight to `main` per CLAUDE.md's start-of-session checklist item 2)

### T1 — Add dedicated test coverage for `settings.ROBINHOOD_EXECUTION_MODE` via `PUT /settings/feature-flags`

**The gap:** `ROBINHOOD_EXECUTION_MODE` (`settings.py:1503-1506`, fail-safe `@field_validator` at
`settings.py:5159-5167`) is a member of `_FEATURE_FLAGS_NON_BOOL_SPECS`
(`api/pilots_api.py:5113-5117`), served as an `"enum"` widget with `options: ["off", "review",
"live"]` through `GET`/`PUT /settings/feature-flags` (`api/pilots_api.py:5140-5164`). This is a
**different** endpoint from the well-tested `PUT /automation/execution-mode` (see the corrections
table above) — nothing in the test suite currently exercises `ROBINHOOD_EXECUTION_MODE` through the
Feature Flags editor's own write path specifically.

**Fix:** add a new test class to `tests/test_pilots_api_tunables.py` (or extend
`TestSettingsSubroutesRealFieldInvariant`'s sibling coverage for `/settings/feature-flags`) covering:
- **Happy-path write**: `PUT /settings/feature-flags` with `{"ROBINHOOD_EXECUTION_MODE": "review"}`
  (plus `confirm=True`, since this is a `settings_keysets.DANGEROUS_KEYS` member) succeeds and the
  value round-trips on the next `GET`.
- **Invalid-enum rejection**: `{"ROBINHOOD_EXECUTION_MODE": "bogus"}` is rejected with
  `rejected["ROBINHOOD_EXECUTION_MODE"] == "invalid_option"` — model this directly on the existing
  `MARKET_DATA_PROVIDER` enum-rejection test at `tests/test_pilots_api_tunables.py:582-589`.
- **Fail-safe coercion**: confirm the underlying `_coerce_robinhood_mode` validator's collapse-to-
  `"off"` behavior is exercised through this endpoint too (a value that clears Pydantic's own enum
  gate — if one exists at this layer — but would be inert if it somehow reached the validator).
- **Dangerous-key confirmation gate**: a write to `ROBINHOOD_EXECUTION_MODE` without `confirm=True`
  is rejected, mirroring `tests/test_pilots_api.py::TestExecutionModeConfirmation`'s pattern
  (`tests/test_pilots_api.py:3374`) but against the Feature Flags route instead of
  `/automation/execution-mode`.

**Acceptance criteria:** all four cases pass; the new tests fail if `ROBINHOOD_EXECUTION_MODE` is
temporarily removed from `_FEATURE_FLAGS_NON_BOOL_SPECS` (spot-check locally, do not commit).

**Docs:** none required — this is test-only coverage of an existing, already-documented setting.

---

### T2 — Doc-drift fix (already handled elsewhere)

A separate, parallel task in this same batch of work is already correcting doc drift between
`docs/plans/MCP_EXPANSION_PLAN.md` and `docs/plans/MCP_EXPANSION_WALKTHROUGH.md`. Do not re-describe
or re-implement that fix here — it is out of scope for this plan and tracked independently.

---

## "Everything else" tier (execution/orchestrator-adjacent — branch + PR, plan reviewed before code, per CLAUDE.md)

### T3 — Fix the `ExecutionLadder` widget bug

**The bug, confirmed live:** `webapp/src/screens/AgenticTrading.tsx`'s `ExecutionLadder` component
(defined at line 410, invoked at line 341 as `<ExecutionLadder currentMode={data.mode} />`) renders a
4-step **platform-mode** ladder — `const steps = ["advisory", "simulation", "paper", "live"]`
(line 411) — but `data.mode` is actually the 3-step **queue-mode** value (`off` / `review` / `live`)
returned by `GET /agentic/status` (`api/pilots_api.py:2714`, `"mode": queue_summary["mode"]`), itself
sourced from `execution/queue_builder.py`'s `VALID_MODES = ("off", "review", "live")`
(`execution/queue_builder.py:69`). `steps.indexOf("off")` and `steps.indexOf("review")` both return
`-1` (`currentIndex = steps.indexOf(currentMode)`, line 413), so the widget silently shows **no step
highlighted** in both the default (`off`) and most common (`review`) states — only the rare `"live"`
case happens to line up, by coincidence, since `"live"` is present in both ladders. The component's
own inline comment even acknowledges this ("Handle edge cases like 'off' or 'review' by defaulting
appropriately or leaving unhighlighted," line 412) without actually handling it.

**Fix direction:** change the component's step array/labels to the real ladder it is actually
displaying — `["off", "review", "live"]` with labels reflecting what each queue mode means (e.g.
"Off" / "Review" / "Live"), not the aspirational advisory/simulation/paper/live platform-mode
progression that `data.mode` never carries. If a genuine 4-step platform-mode ladder (advisory →
simulation → paper → live) is still a desired future concept for this tab, it needs its own backing
data field from the API — do not leave the widget silently mismatched to a field it was never fed.

**Add regression coverage:** `AgenticTrading.test.tsx` currently has zero coverage of
`ExecutionLadder`'s highlighting behavior. Add a test covering all three real states (`off`,
`review`, `live`) asserting the correct step is visually marked active and no step is left in a
false "nothing highlighted" state for the two most common modes.

**Acceptance criteria:**
- `ExecutionLadder` correctly highlights the current step for all three real `mode` values the API
  can actually return.
- New/updated tests in `AgenticTrading.test.tsx` covering `off`, `review`, and `live`.
- `npm run --prefix webapp typecheck` clean.

**Docs:** update `docs/architecture/webapp-and-gui.md`'s `AgenticTrading.tsx` entry (or add one if
none exists) to describe the corrected ladder semantics, so a future reader doesn't reintroduce the
4-step mismatch.

---

### T4 — Harden `simulation_engine.py`'s optional-dependency handling

**The gap:** `simulation_engine.py` wraps its `vectorbt`/`backtrader` imports in `try/except
ImportError` (lines 21-23 and 26-29) but never sets an availability flag from either branch, and the
module-level `class InstitutionalStrategy(bt.Strategy):` (line 151) references `bt` unconditionally
at import time — if `backtrader` is missing, the class definition itself raises `NameError` at
**module import**, not merely at call time inside `run_backtrader_simulation`/
`optimize_strategy_vectorbt`. Because `investyo_mcp_server.py` imports `run_backtrader_simulation`
and `InstitutionalStrategy` at module level, a lean MCP-only deploy (the platform now has one — see
the generic Ubuntu VPS deploy path added in PR #940) that omits these two dependencies would crash
the **entire MCP server at import time**, not degrade one tool gracefully.

**Fix:** match the `TENSORFLOW_AVAILABLE` pattern already established in `forecasting_engine.py`
(lines 35-40 — `try: import tensorflow ...; TENSORFLOW_AVAILABLE = True; except ImportError:
TENSORFLOW_AVAILABLE = False`):
1. Export `VECTORBT_AVAILABLE`/`BACKTRADER_AVAILABLE` boolean flags from `simulation_engine.py`'s
   existing try/except import block.
2. Make the module-level `class InstitutionalStrategy(bt.Strategy)` definition conditional on
   `BACKTRADER_AVAILABLE` (e.g. define it inside an `if BACKTRADER_AVAILABLE:` block, or give the
   module a no-op placeholder base class when backtrader is absent, whichever keeps downstream
   type-hints/imports from breaking).
3. Guard the `run_backtrader_simulation`/`optimize_strategy_vectorbt` call sites so each degrades
   with a clear log/return rather than a raw `NameError`/`AttributeError` when its dependency is
   missing.
4. Extend `tests/test_simulation_engine.py` to actually exercise the missing-dependency path
   (monkeypatch the flag off, assert graceful degradation) rather than relying solely on
   `pytest.importorskip`-style skipping, which never exercises the absent-dependency branch at all.

**Acceptance criteria:**
- `python -c "import simulation_engine"` succeeds even when `vectorbt`/`backtrader` are not
  importable (simulate via monkeypatching `sys.modules` in a test, not by actually uninstalling them
  from this repo's own dev environment).
- `investyo_mcp_server.py` imports cleanly under the same simulated-absence condition.
- New/updated tests in `tests/test_simulation_engine.py` covering both the present and absent cases
  for each flag.

**Docs:** update `docs/architecture/simulation-eval-reporting.md`'s `simulation_engine.py` entry to
describe the new availability flags and the MCP-server import-safety motivation, and note the fix in
`docs/architecture/observability-and-apis.md`'s `investyo_mcp_server.py` entry (the consumer whose
import-time crash this closes).

---

### T5 — Closed-loop paper mode (the substantial new feature)

**The feature:** wire `agentic-discovery` → advisory cross-reference → `execute_paper_trade` into a
scheduled, paper-only loop — the item `docs/handovers/agentic_trading_synthesis.md:140-143` already
names as "(Future, not started)."

**Design constraints:**
- **Never touches the Robinhood MCP's write tools or `execution/order_manager.py`'s live path** —
  fills exclusively into the existing paper book (`data/paper_account_store.py` via
  `execution/fmp_paper_broker.py`).
- **Reuse the discovery→universe merge that's already automatic every cycle** — `main.py`'s
  `_build_universe()` (`main.py:343`) already merges discovered scan candidates from
  `scan_candidates.json` (`main.py:349`) into the tracked universe every cycle, and
  `pilots/discovery.py::discovery()` (`pilots/discovery.py:100`) already reads that same artifact.
  The only new work is the scoring→paper-execution leg on top of what's already merged — do not
  reimplement the universe merge.
- **New flag `AGENTIC_PAPER_LOOP_ENABLED`, default `False`** — this changes what trades get
  simulated, so it follows this repo's trading-behavior-flags-default-off convention (per
  `CLAUDE.md`'s "New settings default to today's exact behavior" rule for anything that changes
  trading behavior), not the admin-capability-defaults-on convention that applies to non-trading
  write/execution gates.
- **Cadence: hook into `desktop/daemon_runtime.py`'s existing `_timer_loop`** (`_timer_loop` at
  `desktop/daemon_runtime.py:915`), the same way `OPTIONS_0DTE_ENABLED`
  (`desktop/daemon_runtime.py:973`) and `CIRCUIT_BREAKER_ENABLED`
  (`desktop/daemon_runtime.py:690`/`715`) already do — add a new `maybe_run_agentic_paper_loop()`
  method, gated on the flag, that never raises out of the loop (matching the established
  `maybe_update_circuit_breaker()`/`maybe_alert_on_pipeline_stall()` pattern of best-effort,
  dead-letter-safe per-tick work).
- **Must check `output/scan_candidates.json` freshness and skip the cycle if stale** — mirror the
  staleness-checking convention already used elsewhere in this codebase (e.g.
  `execution/queue_builder.py::is_queue_stale`) rather than inventing a new one.
- **Tag every simulated trade with `strategy_id`/`pilot_id`**, matching
  `data/paper_account_store.py`'s existing convention (`strategy_id`/`pilot_id` columns already
  present on `PaperPosition`/`PaperOrder`/`PaperClosedTrade`) — never leave a loop-originated trade
  under the default `"untagged"` bucket, since that bucket carries the documented
  `allow_untagged_fallback` misattribution risk this codebase has already fixed once (see
  `CLAUDE.md`'s PR 872 remediation bullet).
- **Documentation-update step per `CLAUDE.md`'s planning rule**: a new `docs/known_issues/`-adjacent
  write-up (or a `docs/signals/`-style page if the loop's scoring logic warrants one) describing the
  loop's scope and honest limitations, plus surfacing the loop's on/off state and last-outcome on the
  Agentic Trading tab (`webapp/src/screens/AgenticTrading.tsx`) so an operator can see whether it's
  running and what it last did without reading logs.

**Acceptance criteria:**
- `AGENTIC_PAPER_LOOP_ENABLED=False` (default) leaves the daemon's `_timer_loop` byte-identical to
  today's behavior.
- With the flag on, a fresh `scan_candidates.json` produces real paper fills tagged with a
  distinguishing `strategy_id`/`pilot_id`; a stale one is skipped with a logged reason, not a crash.
- No code path under this feature ever imports or calls a Robinhood MCP order-placement tool, or
  `execution/order_manager.py`'s live submission path.
- New tests covering: flag-off no-op, staleness-skip, and a successful paper-fill round trip with
  correct `strategy_id`/`pilot_id` tagging.
- Agentic Trading tab shows the loop's enabled/disabled state and last-run outcome.

**Docs:** the new `docs/known_issues/`- or `docs/signals/`-adjacent write-up above, plus a `CLAUDE.md`
bullet documenting the new `AGENTIC_PAPER_LOOP_ENABLED` setting per the "new setting → docs" rule,
and an update to `docs/handovers/agentic_trading_synthesis.md` marking this item as delivered rather
than "(Future, not started)."

---

## Explicitly out of scope (recorded, not built)

- **Live options order execution** — reverses 8 recorded architecture decisions and 6 hardcoded,
  regression-tested "Advisory-Only Mode: Live ... options order execution is disabled" refusals
  across `pilots/copula_stat_arb.py`, `pilots/dispersion_trading.py`, `pilots/earnings_crush.py`,
  `pilots/paper_broker.py`, `pilots/vol_mispricing.py`, and `pilots/zero_dte_engine.py`, plus the
  `robinhood-execution` skill's equities-only tool scope and `agentic-discovery`'s explicit ban on
  ever calling `place_option_order`/`review_option_order`. The operator chose to keep options
  paper-only. Not planned.
- **SecProve Agent Safety Kit** — unvetted third party, zero mentions anywhere in this repo today.
  This repo already has native equivalents: kill switch (`execution/kill_switch.py`), per-trade and
  concentration caps (`execution/risk_gate.py`'s position/correlation/portfolio-heat checks, plus
  `engine/advisory.py`'s own `CONFIG["max_single_position_pct"]` cap), and
  prompt-injection hardening already fixed 2026-08-24
  (`docs/known_issues/llm_prompt_injection_undelimited_headlines.md`). Not adopted.
- **New narrowly-scoped skills for other Robinhood MCP tool categories** (fundamentals/earnings/
  watchlists) — plausible future work, not concrete enough to scope here.

---

## What Claude Code will audit afterward

Once Antigravity's PR(s) land, Claude Code will independently verify — not just re-read the diff, but
re-run — the following:

1. **T1**: re-run the new `PUT /settings/feature-flags` `ROBINHOOD_EXECUTION_MODE` tests directly;
   confirm they fail if `ROBINHOOD_EXECUTION_MODE` is temporarily pulled out of
   `_FEATURE_FLAGS_NON_BOOL_SPECS` (spot-check locally, do not commit).
2. **T3**: confirm `ExecutionLadder` actually highlights the correct step for `off`/`review`/`live`
   in a real browser check (`/verify-webapp`), not just that the new tests pass — the original bug
   was a *visual* silent failure that a shallow test could still miss.
3. **T4**: run `pytest tests/test_simulation_engine.py -q` directly; separately confirm, via a
   simulated-absence import test, that `investyo_mcp_server.py` no longer crashes at import time when
   `vectorbt`/`backtrader` are unavailable — the whole point of this fix is the MCP-only VPS deploy
   path, so verify the actual failure mode this closes, not just that the module imports in this
   dev environment where both dependencies are already present.
4. **T5**: confirm `AGENTIC_PAPER_LOOP_ENABLED=False` leaves `desktop/daemon_runtime.py`'s
   `_timer_loop` behavior byte-identical to before the change; with the flag on, manually construct a
   fresh and a stale `scan_candidates.json` and confirm the loop fills a real paper trade in the
   fresh case and cleanly skips in the stale case; confirm no code path in the new feature can reach
   a live-order tool by grepping the diff for any Robinhood MCP write-tool name or
   `execution/order_manager.py` import.
5. **Cross-cutting**: re-run the `.claude/skills/run-investyo-mcp/` driver (built in a parallel task)
   to confirm the MCP server still boots and answers a tool call after the changes — this is the
   most direct end-to-end check on T4's fix, since T4's whole motivation is an MCP-server import-time
   crash. Confirm each PR's docs were actually updated per `CLAUDE.md`'s requirement (not just
   planned), confirm `.claude/` plan-artifact filenames are unique/scoped per the naming rule, and
   confirm no unrelated scope creep was bundled into any of the three feature-branch PRs.

## Verification commands (for both Antigravity and the Claude Code audit pass)

```bash
pytest tests/test_pilots_api_tunables.py -k ROBINHOOD_EXECUTION_MODE -q
pytest tests/test_pilots_api.py -k TestExecutionModeConfirmation -q
pytest tests/test_simulation_engine.py -q
npm run --prefix webapp typecheck
npx vitest run src/screens/AgenticTrading.test.tsx --prefix webapp
# T5, once implemented:
pytest tests/test_daemon_runtime.py -k agentic_paper_loop -q
```

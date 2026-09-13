# Decouple Explore From Execute — Walkthrough

Branch: `decouple-explore-execute` (cut from `main` @ `68fc893a`, 2026-09-11).
Executed the plan in `.claude/decouple-explore-execute_implementation_plan.md`
/ `.claude/decouple-explore-execute_task.md` with 4 parallel build agents,
after a live Wave 0 dependency trace performed directly (not delegated) by
the lead session — see `.claude/decouple-explore-execute_wave0_checklist.md`
for the full trace with `file:line` citations.

## What changed and why

This is an audit-and-document task, not a feature build — per the plan's own
framing, "the build here is mostly a trace, a doc, one test, and some copy."
Nothing in `main.py::_build_universe()`, `compute_tracked_universe()`, the
auto-scan's existing override, or any live-order path was touched (confirmed:
`git diff` over every production `.py` file in this PR is empty).

1. **`tests/test_execution_universe_boundary.py`** (new, 13 tests) — the
   structural regression test (plan's WP-A). Proves, via a static AST
   import-boundary guard plus runtime tests, that a symbol reachable only by
   browsing the Symbol Screener or Quick Trade can never enter either
   orchestrator's autonomous universe, and pins the corrected
   (narrower-than-assumed) default scope of the options auto-scan's
   fully-automated path. Includes a real, performed (not merely asserted)
   break-then-revert proof against both `data/portfolio_sync.py` and
   `execution/options_paper_executor.py`, recorded in the file's own trailing
   comment.
2. **`docs/architecture/execution-boundary.md`** (new) — the boundary
   write-up (WP-B) plus the auto-scan override's policy statement (WP-D),
   merged into one document per the plan's instruction. Confidence-tiered
   ("traced against live code" / "newly verified this session" / "not
   verified") throughout, matching this repo's `docs/FMP_INTEGRATION.md`
   convention.
3. **`docs/known_issues/options_auto_scan_default_universe_gap.md`** (new) +
   one new row in `docs/known_issues/README.md` — a dedicated write-up of the
   one genuine, previously-undocumented finding this session surfaced (below).
4. **UI legibility copy** (WP-C) — one honest sentence added at three
   existing insertion points: `ExplainTickerDrawer.tsx`'s untracked-symbol
   branch, `PaperBroker.tsx`'s Quick Trade panel, and `SymbolScreener.tsx`'s
   existing hand-off copy — reusing each component's existing visual
   treatment, no new design system introduced. Corresponding test files
   extended, not replaced.
5. **`CLAUDE.md`/`AGENTS.md`** (WP-E) — one new `docs/architecture/` index
   row and one new dated (2026-09-11) convention bullet, auto-mirrored
   byte-identical between the two files via the repo's own
   `sync_agent_docs.sh` hook (confirmed via `diff`, exit 0).

## The one genuine finding (not assumed going in)

The original plan's §0 assumed the options auto-scan's *default* (no
operator-override) path shared `compute_tracked_universe()`'s full breadth,
and only its *manual override* was gated by `PAPER_OPTIONS_AUTO_EXECUTE_ENABLED`.
Both assumptions were wrong, traced live this session:

- The manual override endpoint (`POST /pilots/paper-broker/strategy-options/execute`)
  is gated by `PAPER_BROKER_WRITES_ENABLED` + a command token — not
  `PAPER_OPTIONS_AUTO_EXECUTE_ENABLED`, which gates a different path.
- The fully-automated daemon-cycle path (gated by
  `PAPER_OPTIONS_AUTO_EXECUTE_ENABLED`) never receives a `symbols` override
  and instead falls through to a raw, unvalidated split of the
  `settings.WATCHLIST` env var alone — missing `watchlist.txt`, held
  positions, `DEFAULT_TICKERS`, and discovered scan candidates entirely.

This is disclosed as a real, narrower-than-expected coverage gap (never a
boundary leak — it's still bounded to an operator-set value, and it can only
under-cover, never reach an untracked/browsed symbol) in both the new
boundary doc §5 and its own dedicated known_issues write-up. Per the plan's
explicit scope, **fixing it is out of scope for this PR** — it is disclosed,
not patched, and left to a dedicated follow-up.

## Verification performed by the integrating (lead) session, independently

Every claim below was re-run directly, not taken on an agent's word:

- `python3 -m pytest tests/test_execution_universe_boundary.py -q` → **13
  passed** (also re-run with the real project `.venv` and with the sandbox
  disabled — see note below on the two unrelated failures encountered along
  the way).
- `python3 -m pytest tests/test_execution_universe_boundary.py
  tests/test_production_steps_universe.py tests/test_portfolio_sync.py
  tests/test_options_paper_executor.py tests/test_options_lifecycle.py -q`
  (using `/Users/kevinlee/Stockpy-live/.venv`, sandbox disabled for the numba
  cache-write reason below) → **87 passed**.
- `git diff --stat -- '*.py' ':!tests'` → empty (zero production Python
  changed).
- `npm run -s typecheck` (webapp) → clean, zero output.
- `npx vitest run src/components/ExplainTickerDrawer.test.tsx
  src/screens/PaperBroker.test.tsx src/screens/SymbolScreener.test.tsx` →
  **3 files, 56 tests, all passed**.
- `diff CLAUDE.md AGENTS.md` → exit 0, byte-identical.
- Read every new/changed file in full and fixed two stale disclaimers found
  in `docs/architecture/execution-boundary.md` and the new known_issues doc:
  both were written by WP-B in parallel with WP-A and, at write time,
  correctly said the new test file "did not exist yet" / "no regression test
  currently pins this" — true when written, stale once WP-A's test landed.
  Updated both to reflect the test's real, confirmed-passing final state
  rather than leaving an accurate-at-the-time-but-now-wrong disclaimer in a
  merged PR.

**Environment note, not a code issue**: an initial run of
`tests/test_production_steps_universe.py` against this worktree failed 6
tests with a `numba`/`pandas_ta` caching `RuntimeError` — traced to `numba`'s
`@njit(cache=True)` trying to write a disk cache next to `pandas_ta`'s
installed files inside `/Users/kevinlee/Stockpy-live/.venv` (a different
directory than this worktree, and outside the sandbox's default write
allowlist). Confirmed this is a pure sandbox/environment artifact, not a
regression from this PR's changes, by re-running the identical suite with
the sandbox disabled: all 6 passed. No production file review or fix was
needed for this — it's a local sandbox quirk of running tests from a git
worktree against a `.venv` that lives in a sibling checkout.

## Honesty checklist (plan §5) — self-assessed after independent review

- [x] The boundary doc states only what was actually traced live this
      session or independently spot-checked by its author — confirmed by
      reading every citation.
- [x] The auto-scan exception is documented as a confirmed, deliberate,
      already-shipped design decision — not downplayed, not flagged as a bug
      to close.
- [x] The structural test's break-then-revert proof was actually performed
      (real pytest failure output captured, both files reverted to a clean
      `git diff`) — confirmed by reading the test file's own trailing record
      and independently re-confirming the current repo state is clean.

## Claude audit protocol — scaled to 4 agents (not the plan's 6-8), self-audit by the lead session in place of separate auditor agents

The user asked for 4 agents. Given that, the redundant-audit role the plan's
§6 assigns to 6-8 *separate* auditor agents was instead performed by this
integrating session directly, after all 4 build agents completed:
independently re-running every test, re-reading every new doc end to end,
cross-checking the boundary doc's and known_issues doc's claims against the
same live grep/read the lead session had already performed for Wave 0
(not merely trusting either agent's self-report), and fixing the two stale
disclaimers found along the way. This is a real, if smaller-scale, audit
pass — not a rubber stamp — but is not equivalent to the plan's full
independent-blind-re-trace protocol; if that fuller scale is wanted, it
would need dedicated auditor agents to be spun up as a follow-up.

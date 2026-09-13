# Known issue (2026-09-13, fixed same day): the `robinhood-execution` skill instructed an agent to read/write the execution queue and ledgers via a literal repo-relative `output/...` path

**Status: fixed.** Found while re-verifying the (separate, sibling) fix for
`shared/robinhood_execution_panel.py`'s repo-relative `EXECUTION_QUEUE_PATH`
(see `docs/known_issues/execution_queue_panel_repo_relative_path.md`, landed
in [PR #1039](https://github.com/kevinmarko/Stockpy/pull/1039)) — that fix's
own author flagged this as "the SAME underlying bug in a much more
safety-relevant place that was explicitly out of scope for that fix."

## What happened

`.claude/skills/robinhood-execution/SKILL.md`, its `.agents/skills/
robinhood-execution/SKILL.md` Antigravity mirror, and `.claude/commands/
rh-execute.md` all instructed an agent to "Read `output/execution_queue.json`",
check for `output/KILL_SWITCH`, and append to `output/execution_placed.jsonl`
/ `output/execution_receipts.jsonl` — literal, repo-relative paths.

This is the same bug class as `data/historical_store.py` (#718),
`forecasting/forecast_tracker.py` (#720), and
`shared/robinhood_execution_panel.py` (see the sibling doc above), but with
a materially different mechanism: those three are Python modules whose
`Path(__file__)`-anchored default silently diverged from
`settings.OUTPUT_DIR`. This skill has **no Python import** in its execution
path at all — it is followed by a Claude Code (or Antigravity) agent session
using its `Read`/`Write`/`Bash` tools directly. A literal string like
`output/execution_queue.json` in the skill's prose resolves against
**whatever the agent's current working directory happens to be** — the
repo/worktree root the session was launched in — never against
`settings.OUTPUT_DIR` (default `~/.stockpy_local/output`, since PR #718).
This repo runs many concurrent git worktrees, so the two can diverge for any
operator, on every single invocation of this skill, with no code-level
signal that anything is wrong.

## Real impact

Confirmed live on this machine at the time of the fix:

- The real, current, `LOCAL_DATA_ROOT`-anchored queue
  (`~/.stockpy_local/output/execution_queue.json`) held a live `mode: "live"`
  queue with 5 real gated intents, generated 2026-08-21.
- The primary checkout's repo-root `output/` directory held only a **stale**
  Aug-6 snapshot; this worktree had neither file at all.

The skill's own existing staleness check (refuse to place if the queue's
`generated_at` is more than ~30 minutes old) provides some protection
against the specific case of a stale-but-present repo-root file — an agent
that read the Aug-6 snapshot would have correctly refused to place anything,
a fail-safe direction, not a wrong-trade one. But in a fresh worktree with no
repo-root `output/` directory at all (this worktree's exact state), the
agent would instead hit its own hard stop #2 ("queue missing — tell the
operator to run `main.py`") even though a live, real, placeable queue
existed the whole time at the correct location — making the skill
effectively non-functional against a real `LOCAL_DATA_ROOT`-anchored
deployment, silently, with no error message pointing at the real cause.

## The fix

Both `SKILL.md` copies and `rh-execute.md` now resolve the output directory
**once**, at the very start of the session (`SKILL.md`'s new Prerequisites
step 1), via:

```bash
python3 -c "from settings import settings; print(settings.OUTPUT_DIR)"
```

with a documented `.venv`-aware fallback and an explicit instruction to stop
and ask the operator rather than guess if resolution fails. The result is
named `$OUTPUT_DIR` and reused for every subsequent reference to the four
artifacts this skill touches — the queue, the kill switch, and both
ledgers — mirroring the "resolve once, reuse" pattern
`execution/receipts_store.py::_resolve_output_dir` and
`shared/robinhood_execution_panel.py`'s own fix already establish on the
Python side. `rh-execute.md` does not duplicate the recipe; it defers to the
skill's own Prerequisites step 1 (single source of truth, no risk of the
command and the skill drifting on the exact command line).

The `.agents/` copy was rebuilt from the corrected `.claude/` copy with its
existing porting-note HTML comment preamble reinserted unchanged, keeping
the two bodies identical (per this repo's established, if unautomated,
convention for this skill — see `docs/known_issues/skill_directory_manual_copy_drift.md`
and `tests/test_skill_directory_parity.py`).

## Verification

- `tests/test_robinhood_e2e.py::TestSkillMdInvariantsPinned::
  test_skill_md_never_references_bare_repo_relative_output_path` — asserts
  none of the four bare `output/...` literals appear anywhere in either
  `SKILL.md` copy or `rh-execute.md`. Confirmed to genuinely fail against the
  pre-fix content (checked directly against `git show
  main:.claude/skills/robinhood-execution/SKILL.md`, which contains all four).
- `tests/test_robinhood_e2e.py::TestSkillMdInvariantsPinned::
  test_skill_md_resolves_output_dir_via_settings` — asserts the concrete
  recipe (`settings.OUTPUT_DIR`, `from settings import settings`,
  `$OUTPUT_DIR`) is actually present in both `SKILL.md` copies, not just that
  the bad literal is gone.
- The pre-existing `test_skill_md_contains_required_safety_invariants` test
  (pinning the one-confirmation-per-order / kill-switch / idempotency
  invariant phrases, including the bare substring `execution_placed.jsonl`)
  still passes — every pinned phrase survives inside its new
  `$OUTPUT_DIR/execution_placed.jsonl`-style reference.
- This repo's Python 3.14 sandbox could not run `pytest` on
  `tests/test_robinhood_e2e.py` directly — collection fails on an unrelated,
  pre-existing `numba`/`pandas_ta_classic` caching error reproducible via a
  bare `import shared.help_content` (the same environment issue disclosed in
  `docs/known_issues/execution_queue_panel_repo_relative_path.md`'s own
  verification section). The three assertions above were instead verified
  directly against the file contents with a standalone script exercising the
  identical logic, including the revert-and-rerun proof against the
  real pre-fix `SKILL.md` from `main`.

## Related

- `docs/known_issues/execution_queue_panel_repo_relative_path.md` — the
  sibling fix (`shared/robinhood_execution_panel.py`,
  [PR #1039](https://github.com/kevinmarko/Stockpy/pull/1039)) that
  surfaced this issue as explicitly out of scope for itself.
- CLAUDE.md's `settings.LOCAL_DATA_ROOT` bullet — the migration this
  incident is a gap in.
- `docs/known_issues/skill_directory_manual_copy_drift.md` — why the
  `.claude/`/`.agents/` skill copies need manual, not automatic, parity.

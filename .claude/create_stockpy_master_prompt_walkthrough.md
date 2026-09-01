# Walkthrough: Stockpy Master Prompt Skill Creation

## Overview

Created the `stockpy-master-prompt` skill so operators can reference/load the master session prompt directly in Antigravity, Claude Code, Cursor, Jules, or any agent session without manually pasting the full prompt text every time.

## Key Changes

1. **Antigravity Skill**: Created `.agents/skills/stockpy-master-prompt/SKILL.md`.
2. **Claude Code Skill**: Created `.claude/skills/stockpy-master-prompt/SKILL.md`.
3. **Parity Addition**: Added `.agents/skills/stockpy-quant-integrity/SKILL.md` to ensure Antigravity can directly resolve the reference cited in §3 item 2 of the startup ritual.
4. **Architecture Documentation**: Updated `docs/architecture/simulation-eval-reporting.md` to include `stockpy-master-prompt` in the repository skills roster.

## Verification

- Ran `pytest tests/test_robinhood_e2e.py` (15/15 passed).
- Ran `pytest tests/test_discovery_skill.py` (10/10 passed).
- Verified skill frontmatter schema and markdown rendering.

## 2026-09-01 audit+fix pass (2-agent review of this PR against live repo)

A follow-up audit (two independent background agents, one auditing the
`stockpy-quant-integrity` mirror, one auditing `stockpy-master-prompt`
against the original plan text and live repo) found this PR shipped
several stale/inaccurate claims — the exact CONSTRAINT #4-shaped failure
these skills exist to prevent. Neither original pytest run above would
have caught any of this; both tests are unrelated skill-invariant checks.
Fixed in the same session:

- **`.agents/skills/stockpy-quant-integrity/SKILL.md` was not actually a
  mirror of `.claude/skills/stockpy-quant-integrity/SKILL.md`** despite the
  commit message's claim. It shipped an already-superseded claim that
  `earnings_crush`/`dispersion_trading`/`zero_dte_engine`/`gamma_scalper`/
  `copula_stat_arb` "have zero `STRATEGY_REGISTRY` entries" — false as of
  live `scripts/refresh_validations.py` (all six are registered, either
  with real adapters or as explicit `_build_ungateable_adapter` stubs).
  Re-synced to `.claude/`'s correct, current text; the two files are now
  byte-identical again (`diff` exit 0).
- **`stockpy-master-prompt/SKILL.md`'s §7 "Known open gaps"** copied the
  plan text verbatim without re-verifying it, and by merge time
  (2026-09-01) two of its four bullets were already stale: the 0DTE
  15:45 ET exit gate had been wired into `desktop/daemon_runtime.py`'s
  `_timer_loop` three days earlier (`fix-known-open-gaps`, merged
  2026-08-29), and the "zero `STRATEGY_REGISTRY` entries" claim was the
  same stale claim as above. §7 was rewritten to stop restating specific
  numbers/statuses that go stale between sessions, and instead point at
  `stockpy-quant-integrity`'s own maintained "Currently open" section and
  `docs/known_issues/README.md`. The Robinhood MCP live-order bullet was
  independently re-verified as still accurate and kept, now citing
  `docs/known_issues/robinhood_confirmation_gate_is_prose_only.md`.
- **§1's "~28 modules" claim** was unverifiable against any doc in this
  repo and doesn't match actual module counts by any measure checked
  (49 top-level `.py` files, 65 `pilots/*.py`, 27 `signals/*.py`, etc.) —
  replaced with a pointer to `docs/architecture/*.md` instead of a
  headline number.
- **`docs/architecture/simulation-eval-reporting.md`'s new skills-roster
  line omitted `run-investyo-mcp`** (a `.claude/`-only skill that existed
  at commit 4401e97f, the exact commit this PR's own diff was made
  against) despite the commit message claiming "the full repository
  skills roster." Fixed, and the line now explicitly states which skills
  are `.agents/`-only, `.claude/`-only, or true mirrors, since the two
  directories were never actually a mirrored set.
- Removed dead personal `file:///Users/.../antigravity/worktrees/...`
  links from this walkthrough and the companion implementation plan (they
  resolved only inside the authoring agent's own transient worktree).

No code/runtime files were touched — this addendum and its fixes are
scoped entirely to `.claude/`, `.agents/`, and `docs/architecture/`. Of
those, only `.claude/` config is one of the categories CLAUDE.md's
Branch Workflow §2 low-risk carve-out actually names ("docs, `.claude/`
config (settings, hooks, skills, agents), comments, test-only additions,
and other non-behavioral edits — may be committed directly to `main`");
the rule text doesn't separately name `.agents/`, even though these
`.agents/` edits are the same kind of non-behavioral doc change in
substance.

## 2026-09-01 follow-up (independent code review, reconciled with PR #972)

A separate code review of merged PR #970 found the same two core bugs the
audit above already fixed, discovered independently and concurrently —
PR #972 landed on `origin/main` while this review was still in progress.
Rather than duplicate that fix, this pass synced to `origin/main` and added
what wasn't there yet:

- **`tests/test_skill_directory_parity.py`** — the audit above notes "no
  test in the repo references these skill files by name" and that the repo
  Python test suite couldn't be run in that environment. This pass added a
  standalone (no project dependencies beyond `pytest`) regression test
  asserting `stockpy-master-prompt` and `stockpy-quant-integrity` — the
  only two skills confirmed to have no per-platform porting-note preamble
  and to be intended as literal byte-identical mirrors — stay in sync.
  Verified both ways: 3/3 passing against the fixed state, and a scratch
  mutation of one copy (reverted immediately) reproduces exactly the
  failure this incident is about.
- **A second, real, previously-unfound divergence.** Sweeping every skill
  present in both `.agents/skills/` and `.claude/skills/` found 8 already
  differing by a consistent ~8-line HTML-comment "ported from the Claude
  Code sibling skill" preamble. Stripping that preamble confirmed 7 of the
  8 are genuinely body-identical (a legitimate porting note, not drift) —
  but `.agents/skills/agentic-discovery/SKILL.md` had a real, wrong claim
  beyond the preamble: it told an Antigravity agent that `WATCHLIST` takes
  precedence over `watchlist.txt` ("appending to `watchlist.txt` would be
  silently ineffective"), while `main._load_watchlist()`'s own docstring
  says the two are unioned, deduped, with neither taking precedence —
  confirmed against `main.py:267-286` directly. Fixed to match the correct
  `.claude/` text.

## 2026-09-01 code-review pass (8 findings, 7 fixed)

A `/code-review` at high effort over commits `640f0f03..2038dd6e` (this
whole `stockpy-master-prompt`/`stockpy-quant-integrity` chain, PRs
#970/#972/#973) found 8 more issues, all in files this chain itself
added or edited — the same "self-verification gap" pattern documented
above, one layer deeper. 7 were fixed in this pass, verified against the
actual repo rather than assumed:

- **CVaR-placeholder example misattributed** (`.claude/skills/stockpy-
  quant-integrity/SKILL.md:32` + `.agents/` mirror): the "never fabricate
  a metric" bullet pointed at `sizing/hrp_cvar_optimizer.py`'s CVaR field
  as the site of the fabricated `"cvar_95": float(0.05), # placeholder`;
  `git log -S 'cvar_95": float(0.05)'` shows that string only ever
  existed in `api/pilots_api.py`'s HRP/CVaR endpoint handler, never in
  the optimizer module. Corrected in both mirror copies.
- **Startup-ritual step 1 referenced a nonexistent memory tool/path**
  (`stockpy-master-prompt/SKILL.md:130` + mirror): `memory_read
  /areas/stockpy.md` doesn't resolve to any tool or file in this Claude
  Code session (verified: no `memory_read` tool, no `/areas/` directory
  anywhere under `~/.claude/`, no `stockpy.md`). Reworded to describe the
  memory mechanism generically instead of assuming one specific,
  unverified tool call works across every target platform.
- **`tests/test_skill_directory_parity.py`'s sanity check had a
  coverage-gap blind spot**: `test_mirrored_skill_set_is_non_empty` only
  asserted the mirror-list intersection was non-empty, so one of the two
  `EXACT_MIRROR_SKILLS` silently dropping out of coverage (e.g. a
  directory rename) would leave it passing. Now asserts
  `set(EXACT_MIRROR_SKILLS) - set(_mirrored_skill_names())` is empty;
  verified it correctly fires when one mirror is simulated missing.
- **Same test file's docstring misdescribed pytest's own behavior**: it
  claimed an empty `parametrize` list "would make the parity test above
  silently pass on zero cases" — reproduced directly that pytest instead
  reports `SKIPPED (got empty parameter set)`. Docstring corrected.
- **First-differing-line diff logic replaced with `difflib.unified_diff`**
  in the same file — the old manual scan only ever reported the single
  first differing line, requiring a fix-rerun cycle per divergent region
  (the real incident had two in one file). Verified via a scratch
  two-region mutation (reverted) that the new failure message surfaces
  both regions in one run.
- **`tests/test_robinhood_e2e.py`'s `TestSkillMdInvariantsPinned` only
  ever read `.claude/skills/robinhood-execution/SKILL.md`**, falling back
  to the `.agents/` copy only when `.claude`'s was missing — which it
  never is, so the `.agents/` copy's safety-invariant phrasing (gating
  live order confirmation) had zero test coverage, and it's one of the 8
  skills the new parity test explicitly declines to cover. Widened to
  check every copy that exists; verified both currently pass.
- **Walkthrough (this file) overstated a CLAUDE.md carve-out**: an
  earlier addendum claimed `.agents/` changes fall within CLAUDE.md's
  "may be committed directly to `main`" low-risk carve-out; the rule
  text names only "docs, `.claude/` config (settings, hooks, skills,
  agents), ..." — `.agents/` isn't separately named. Corrected to state
  that precisely (moot for this PR either way, since it's going through
  a numbered PR, not a direct push).

**Follow-up in the same session: the 8th finding (altitude) fixed too.**
The initial pass left this one `skipped` on the theory that a live
`PostToolUse` hook change couldn't be safely trigger-tested in this
sandbox. That theory was wrong — the hook scripts just read a JSON blob
on stdin and do plain filesystem `cp`, which can be exercised directly
without a real Claude Code/Antigravity harness. Both
`.claude/hooks/sync_agent_docs.sh` and `.agents/hooks/sync_agent_docs.sh`
were extended with a `MIRRORED_SKILL_NAMES` case (kept in sync with
`tests/test_skill_directory_parity.py::EXACT_MIRROR_SKILLS` by comment
cross-reference) that additionally matches `.claude/skills/<name>/
SKILL.md` <-> `.agents/skills/<name>/SKILL.md` for the two confirmed
exact-mirror skills, alongside the pre-existing `CLAUDE.md`/`AGENTS.md`
case (left untouched in logic and message format). Guarded so it never
creates a brand-new destination file and never fires for any other
skill (verified against `agentic-discovery` and against a same-named
`SKILL.md` at an unrelated path).

Verified end-to-end, not just read for plausibility:
- Editing `.claude/skills/stockpy-master-prompt/SKILL.md` via the real
  `Edit` tool auto-fired the registered `.claude/hooks/sync_agent_docs.sh`
  `PostToolUse` hook and correctly synced the change to (and, on revert,
  back out of) the `.agents/` copy, both confirmed via `diff`.
- `.claude/hooks/sync_agent_docs.sh` was also invoked directly (crafted
  stdin JSON matching its real input contract) to confirm: the
  `CLAUDE.md`/`AGENTS.md` case is unchanged (identical `systemMessage`
  output); a mirrored-skill SKILL.md edit syncs correctly; a
  non-mirrored skill (`agentic-discovery`) is correctly ignored (file
  hash unchanged); a same-named `SKILL.md` at a decoy path outside the
  real skill directories is correctly ignored.
- `.agents/hooks/sync_agent_docs.sh` was invoked directly (crafted stdin
  JSON matching its `toolCall.args.TargetFile` contract) in both
  directions: the write-blocked direction (`.agents` edited -> `.claude`
  dest) reached the correct branch and failed only on this session's own
  Bash-tool sandbox protection of `.claude/skills/`, not on hook logic;
  the writable direction (`.claude` edited -> `.agents` dest) completed
  a real `cp` and was confirmed via `diff`.
- All test mutations were made against scratch backups and fully
  reverted; final state re-confirmed byte-identical for both mirror
  pairs and `CLAUDE.md`/`AGENTS.md`, `tests/test_skill_directory_parity.py`
  passing 3/3, and both hook scripts passing `bash -n` syntax checks.

No findings left unfixed from the original 8; all 8 are `fixed`.

Verification for this pass: `python3 -m pytest tests/test_skill_directory_parity.py -v`
— 3 passed (including a scratch two-region-drift mutation and a
simulated partial-coverage-loss check, both reverted before commit); the
widened `robinhood-execution` invariant check verified to pass against
both real copies via a standalone script (`tests/test_robinhood_e2e.py`'s
own `pytest` collection independently fails in this sandbox on a
pre-existing, unrelated `numba`/`pandas_ta_classic` import error under
system Python 3.14 — not something this change introduced or could fix,
since this repo requires Python 3.12 and no `.venv` exists in this
worktree); plus the hook end-to-end verification described above.

Full reconciled write-up: `docs/known_issues/skill_directory_manual_copy_drift.md`.
Verification: `python3 -m pytest tests/test_skill_directory_parity.py -v`
— 3 passed; `diff` confirms `agentic-discovery`'s two copies are now
body-identical beyond the (legitimate, untouched) porting preamble.

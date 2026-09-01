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
scoped entirely to `.claude/`, `.agents/`, and `docs/architecture/`, all
within the "may be committed directly to `main`" low-risk carve-out in
this repo's `CLAUDE.md` Branch Workflow §2.

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

Full reconciled write-up: `docs/known_issues/skill_directory_manual_copy_drift.md`.
Verification: `python3 -m pytest tests/test_skill_directory_parity.py -v`
— 3 passed; `diff` confirms `agentic-discovery`'s two copies are now
body-identical beyond the (legitimate, untouched) porting preamble.

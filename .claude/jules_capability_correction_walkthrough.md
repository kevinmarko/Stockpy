# Walkthrough: Jules Capability Model Correction

## Overview

The operator confirmed a significant correction: **Jules (Google's coding-agent integration) can only audit/review an existing PR or codebase — it cannot write new code or open a PR from a prompt alone.** This directly contradicted this codebase's entire Jules integration, which was built (and documented, in 17 files) around the opposite assumption: "given a prompt and a connected GitHub repo/branch, Jules writes code and opens a real, unsupervised PR" (hardcoded via `"automationMode": "AUTO_CREATE_PR"`).

This PR corrects that everywhere — both the documentation making the claim and the actual code that would have attempted to exercise a capability that doesn't exist.

## What changed

**The core fix**, applied once and referenced identically everywhere: `data/jules_client.py::dispatch_session()` now unconditionally raises a new `JulesCapabilityNotAvailable(RuntimeError)` as the very first thing it does — before validating any argument, before any dedup/lock logic, before any network call — regardless of `confirm`/`force`/`JULES_ENABLED`. The confirm-gate exception, dedup ledger, and advisory lock that used to support the dispatch flow are removed as dead code. `list_sources()`/`format_sources()` are untouched — genuinely valid, read-only, and still work (they just enumerate which GitHub repos are connected to the operator's Jules account).

**Every caller updated to match:**
- `scripts/jules_dispatch.py`'s `create-session` CLI subcommand now states its non-functional status in its own `--help` output, before a user even invokes it — and still genuinely calls `dispatch_session()`, letting the real exception surface as a clean error rather than silently no-op-ing.
- `investyo_mcp_server.py`'s `dispatch_jules_task` MCP tool — the single most load-bearing false claim in the whole codebase, since it's the docstring a calling agent reads before deciding whether to invoke the tool — is completely rewritten. The tool stays present (rather than vanishing, which would look like a connection failure) so a caller gets an informative, correct error.

**Documentation, corrected everywhere it made the claim:**
`docs/JULES_INTEGRATION.md` (full rewrite), `.claude/skills/jules-delegation/SKILL.md` + its `.agents/` port (full rewrite), `CLAUDE.md`/`AGENTS.md`'s integration bullet, `.env.example`'s comment block, `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md` (3 substantive spots — a capability-map row, a "prose gates vs. code gates" analysis, and an open-gaps backlog entry), `docs/README.md`'s index entry, `settings.py`'s field descriptions, and `settings_keysets.py`'s `DANGEROUS_KEYS` reasoning for `JULES_ENABLED` (kept as a dangerous key — it still gates the real `list_sources()` credentialed API call — but the reasoning no longer cites a PR-opening risk that doesn't exist).

**`review_summary.md`** — this is a historical operational log, not a living reference doc, so it was NOT rewritten. Instead, a correction note was prepended clarifying that its "Stockpy Jules Queue" framing reflects the same now-corrected wrong belief, and that the actual authorship of the branches/PRs it reviews should not be assumed from the title.

## A disclosed side-finding, not fixed here

While reading `review_summary.md` for the note above, I found it explicitly recommends **rejecting** the PR on branch `fix-n-plus-1-schema-migration-1819301733788490750` (PR #946) due to a real bug — `cursor.execute("BEGIN TRANSACTION;")` inside a session that already has an implicit transaction open would raise `sqlite3.OperationalError: cannot start a transaction within a transaction`. That PR appears to have been merged anyway (confirmed as `state: MERGED` earlier in this session's PR listing). This is a genuine, separate concern the operator should look into — explicitly flagged, not silently fixed or ignored, and out of scope for this PR.

## How this was built

1 inventory agent (found 24 files, 17 needing correction) → 5 parallel fix agents (client code, CLI, MCP tool, primary docs, secondary docs — each given the identical corrected capability statement and exception contract up front so they could run fully in parallel with zero coordination needed) → 2 direct fixes by the orchestrating session (`review_summary.md`'s note; confirming the `.claude/agentic_safety_framework_docs_*` process notes needed no change) → independent re-verification of everything.

## Testing & Validation

- 40 Jules-related tests pass across `tests/test_jules_client.py`, `tests/test_jules_dispatch.py`, `tests/test_investyo_mcp_server.py`.
- `tests/test_settings.py`: 45/45 pass (7 apparent failures on the first run were the pre-existing, unrelated sandbox `numba`/`pandas_ta_classic` cache-locator issue seen throughout this session — confirmed not a regression by re-running with `NUMBA_DISABLE_JIT=1`).
- `docs/settings_field_census.md`/`.json` regeneration checked directly — no diff produced (the census tracks structural liveness/allowlist facts, not description text).
- `CLAUDE.md`/`AGENTS.md` confirmed byte-identical via `cmp` after the edit.
- Verification grep across every corrected doc file confirms zero remaining assertions of the false capability as currently working.
- The CLI fix was additionally verified against a real, non-mocked end-to-end invocation, confirming a clean error with no traceback and no network call attempted.

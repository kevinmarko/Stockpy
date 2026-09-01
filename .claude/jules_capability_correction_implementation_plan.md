# Jules Capability Model Correction — Implementation Plan

## §0 Dependency Check

- Operator-confirmed correction (not inferred): Jules (Google's coding-agent integration) can only audit/review an existing PR or codebase. It cannot write new code or open a PR from a prompt alone — it cannot "build something from nothing."
- Prior to this PR, the entire integration (`data/jules_client.py::dispatch_session()`, hardcoded `automationMode: "AUTO_CREATE_PR"`) was built around the opposite, incorrect assumption, and that assumption was documented as fact across 17 files.
- A full repo-wide inventory (Explore agent, this session) confirmed 24 files reference Jules; 17 needed correction (7 pure docs/config-doc, 3 real code+test files, 2 skill files, plus `review_summary.md` handled separately as a historical-record annotation rather than a rewrite).

## Approach

5 parallel fix agents (client code, CLI, MCP tool, primary docs, secondary docs), each given the identical corrected capability statement and the same new exception contract, so all 5 could run fully in parallel without needing to coordinate on wording. Verified independently by the orchestrating session afterward (full Jules test suite, settings sanity checks, CLAUDE.md/AGENTS.md sync, settings-doc regen check).

### The core code contract (defined once, given identically to all 5 agents)

`data/jules_client.py` gains `JulesCapabilityNotAvailable(RuntimeError)`. `dispatch_session()` keeps its exact existing signature but now unconditionally raises this exception as the very first thing it does — before validating `source`, before any dedup/lock logic, before any network call — regardless of `confirm`/`force`/`JULES_ENABLED`. `list_sources()`/`format_sources()` are untouched: genuinely valid, read-only, capability-agnostic (they just enumerate which GitHub repos are connected to the operator's Jules account).

### Files touched, by domain
- **Core client**: `data/jules_client.py`, `tests/test_jules_client.py` — the exception + neutered dispatch + dead-code removal (confirm-gate exception, dedup ledger, advisory lock — all existed only to support the now-confirmed-nonexistent capability).
- **CLI**: `scripts/jules_dispatch.py`, `tests/test_jules_dispatch.py` — `create-session` subcommand's help text/docstrings corrected to state non-functional status up front; still genuinely calls `dispatch_session()` and lets the real exception propagate.
- **MCP tool**: `investyo_mcp_server.py`, `tests/test_investyo_mcp_server.py` — `dispatch_jules_task`'s docstring (the single most load-bearing false claim, since it's what a calling agent reads before deciding to call the tool) rewritten completely; `list_jules_sources` left unchanged (already accurate).
- **Primary docs**: `docs/JULES_INTEGRATION.md` (full rewrite), `.claude/skills/jules-delegation/SKILL.md` + `.agents/skills/jules-delegation/SKILL.md` (both, full rewrite — near-duplicate files).
- **Secondary docs**: `CLAUDE.md`/`AGENTS.md` bullet, `.env.example`, `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md` (3 substantive spots), `docs/README.md` index entry, `settings.py` field descriptions, `settings_keysets.py` `DANGEROUS_KEYS` reason string.
- **Direct fix, orchestrating session**: `review_summary.md` — NOT rewritten (it's a historical operational record); a correction note prepended clarifying its "Jules Queue" framing reflects the since-corrected wrong capability model, and that the actual authorship of the branches it reviews was not independently re-verified as part of this pass.

## Documentation-update step (explicit, per CLAUDE.md)

Every doc file that made the false claim is listed above and was corrected — this PR's entire purpose IS documentation correction, so the doc-update step and the implementation are the same set of changes. `AGENTS.md` was confirmed to auto-sync via `sync_agent_docs.sh` after the `CLAUDE.md` edit (byte-identical, verified via `cmp`); no manual mirror was needed.

## Verification

- 40 Jules-related tests pass across all 3 test files.
- `settings.py`/`settings_keysets.py` re-verified importable and structurally valid after edits.
- `tests/test_settings.py`: 45/45 pass (the 7 apparent failures on first run were the pre-existing, unrelated sandbox `numba`/`pandas_ta_classic` cache-locator issue seen throughout this session — confirmed by re-running with `NUMBA_DISABLE_JIT=1`, not a regression from this change).
- `docs/settings_field_census.md`/`.json` regeneration checked — no diff (the census tracks structural liveness/allowlist facts, not description text, so the `settings.py` docstring corrections didn't require regenerating it).
- `CLAUDE.md`/`AGENTS.md` confirmed byte-identical via `cmp`.
- Verification grep across all doc files confirms zero remaining assertions of the false capability as currently working — every surviving mention of the old design is explicitly framed as historical/retired.
- The CLI agent additionally ran the real, non-mocked `scripts/jules_dispatch.py create-session --confirm ...` end-to-end and confirmed a clean, informative error with no traceback and no network call.

## Known, disclosed follow-ups (not fixed in this PR — flagged, not silently ignored)

- **`review_summary.md`'s implied authorship is unverified.** This document frames several already-merged PRs (including `fix-n-plus-1-schema-migration-...`, PR #946) as a "Jules Queue" that a reviewing agent audited. Given Jules cannot actually author PRs, who really opened those branches was not independently re-verified as part of this correction pass — flagged to the operator, not resolved here.
- **A separate, unrelated finding surfaced while reading `review_summary.md`**: it explicitly recommends **rejecting** PR #946 (`fix-n-plus-1-schema-migration-1819301733788490750`) due to a real `sqlite3.OperationalError: cannot start a transaction within a transaction` risk, yet that PR appears to have been merged anyway. This is a genuine, separate concern worth the operator's attention — out of scope for this PR, not silently fixed or ignored.
- **No working "Jules audits an existing PR/codebase" dispatch mechanism exists anywhere in this repo.** The corrected docs are honest about this: `list_sources()`/`format_sources()` (enumerating connected repos) is the only capability that remains functional today. Building a genuine review/audit dispatch path (if Jules's real API supports one) is new work this pass does not attempt.

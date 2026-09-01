# NotebookLM Export Walkthrough

## What was built
A dedicated backend Python script `scripts/export_notebooklm.py` was created to serve as an automated data pipeline to Google NotebookLM. 

Since NotebookLM relies on high-quality text grounding rather than JSON payloads, this script extracts the current live state of the trading platform and formats it directly into a highly readable Markdown document (`output/notebooklm_source.md`). 

The export contains three main context areas:
1. **Macro Context:** VIX, 10Y-2Y yield curve spread, and High Yield OAS, resolved from `HistoricalStore.get_macro()` — a DB-cached-with-periodic-FRED-topup read (up to `settings.MACRO_REFRESH_HOURS`, default 12h, stale before it re-fetches), not a real-time feed. The "Current Portfolio" section similarly now surfaces its own `Snapshot As Of` timestamp so a NotebookLM reader can see how fresh the account data actually is, rather than assuming it's live.
2. **Current Portfolio:** Complete point-in-time account snapshot including total equity, buying power, and a detailed list of all open positions (symbols, quantities, average cost, and live market value).
3. **Active Pilot Follows:** A breakdown of active automated trading strategies (`Pilot ID`), allocated amounts, and their status.

The script conforms to **CONSTRAINT #4** (never fabricate data) by outputting "N/A" or omitting items when the underlying data is genuinely missing or the database is cold, rather than coercing it to $0 or 0.

## Integration
- Added to `cli_introspect/targets.py` under the `TARGETS` list so it can be automatically detected by the orchestrator GUI.
- Run via the command line via `uv run python scripts/export_notebooklm.py` or automatically via cron/scheduler.
- Re-built `cli_introspect/command_manifest.json` ensuring the UI recognizes the new command.

## Verification (original PR)
- Ran the script locally against the operator's real, live `~/.stockpy_local/quant_platform.db` (not a test fixture — "real test data" in an earlier draft of this walkthrough was misleading wording) and observed real output (`$43,115.34` equity, VIX `14.51`, `10` active Pilot follows).
- Visually reviewed the output Markdown structure at `~/.stockpy_local/output/notebooklm_source.md`.
- **This was a single, manual, unverified-by-a-second-party local run with zero automated test coverage shipped in the original PR diff** — per this repo's CLAUDE.md, a self-reported result like this is untrusted input until an independent pass confirms it. See the audit section below for that independent pass.

## 6-Agent Audit Findings & Remediation (post-PR)

An independent 6-agent audit (honesty/CONSTRAINT #4-#6 auditor, mechanical test-writer, adversarial code reviewer, docs/single-source-of-truth auditor, adversarial edge-case breaker, CLAUDE.md process-compliance auditor) was run against the PR's diff. Findings and disposition:

1. **CONFIRMED bug, fixed — `cli_introspect/command_manifest.json` path pollution.** The PR's manifest regeneration was run from inside a Gemini Antigravity worktree (`~/.gemini/antigravity/worktrees/...`), baking that machine-specific absolute path into two UNRELATED commands' (`preflight_check.py`, `track_record_status.py`) `--output-dir` defaults in the shared, committed manifest. Fixed by hand-splicing only the new `export_notebooklm.py` entry onto `main`'s untouched manifest instead of doing a wholesale regeneration from a foreign checkout — the diff against `main` is now exactly: the new command entry, `generated_at`, `command_count`.
2. **CONFIRMED bug, fixed — `store` variable cross-section coupling.** `store = HistoricalStore(readonly=True)` was constructed inside the Macro Context `try` block and reused, out of scope, inside the separate Portfolio `try` block. If construction itself ever raised, the Portfolio section would hit a masked `NameError` instead of a clean, honest degrade. Fixed: `store` is now constructed once up front with its own guard; both sections check `if store is None` explicitly.
3. **CONFIRMED design smell, fixed — module-top `api.pilots_api` import.** Importing `api.pilots_api` at module scope pulls in a full FastAPI app plus a large transitive module graph (`gui/*`, `llm/*`, `ml/*`, `agents.rag_orchestrator`, ...). A hard failure anywhere in that graph would have crashed the ENTIRE script before the Macro Context / Active Pilot Follows sections — which don't need it — ever ran, defeating the script's own per-section degrade-don't-crash design. Fixed: the import is now lazy, inside the Portfolio section's own `try` block, so a failure there degrades only that one section (CONSTRAINT #6).
4. **Disclosed, not fixed — non-atomic write.** `out_path.write_text(...)` was a plain (non-atomic) write, unlike this repo's established temp-file+rename convention. Low real-world impact (confirmed zero other readers of `notebooklm_source.md` anywhere in the codebase — it's a manually-triggered, manually-uploaded artifact, not a live-read file like `state_snapshot.json`), but fixed anyway to match convention and for free safety against a mid-write kill.
5. **Disclosed caveat, documented, not a bug — `get_macro()` can still make a live FRED network call on a `readonly=True` store** before its write attempt fails closed at the DB layer (`attempt to write a readonly database`, caught internally, falls back to cached data). This is pre-existing, shared behavior of every `get_macro()` caller, not specific to this script; now called out in an in-file comment.
6. **CONFIRMED, fixed — no automated tests shipped.** `tests/test_export_notebooklm.py` added: 23 tests covering the happy path, all three sections' independent degraded paths, `HistoricalStore` construction failure, and — most importantly — that a genuine `0`/`0.0` renders as an honest zero while `None`/`NaN` renders as `"N/A"`, in both directions (this distinction was verified NOT to be accidentally conflated anywhere).
7. **No issues found**: CONSTRAINT #4/#6 honesty (every field traced to its real source, no hardcoded/mocked values, no fabricated zero anywhere in the actual code path), `cli_introspect/targets.py` wiring, edge cases (cold DB, empty follows, zero/NaN position fields, missing/unwritable `OUTPUT_DIR`, idempotent re-run), and no undocumented duplication with the existing frontend `NotebookMLExport.tsx` widget (a genuinely separate, non-overlapping export path — JSON/browser vs. Markdown/backend, confirmed no shared logic to drift).
8. **Process gap, fixed** — the Implementation Plan was missing CLAUDE.md's required explicit documentation-update-step conclusion; added (conclusion: no `docs/` file needs a change, with reasoning). This walkthrough's own "real test data" wording (misleadingly implying a test fixture, when it was actually the operator's real live account data) has been corrected above.

All fixes verified: `python3 -m pytest tests/test_export_notebooklm.py tests/test_command_manifest_freshness.py tests/test_build_command_manifest.py -q` passes, and the script was re-run end-to-end against the real live DB after every fix.

# NotebookLM Automated Pipeline Export

Implement an automated script to format platform data into a structured Markdown document specifically for ingestion into Google NotebookLM. 

## User Review Required

> [!IMPORTANT]
> Since NotebookLM accepts Markdown natively and performs incredibly well with it, this backend export will generate a rich `.md` file instead of JSON (which the frontend `NotebookMLExport.tsx` currently provides). Is Markdown format exactly what you prefer for your NotebookLM source?

> [!NOTE]  
> The new script will be placed in `scripts/export_notebooklm.py` so it can be run manually, via cron, or integrated into the orchestrator daemon. It will output to `output/notebooklm_source.md`.

## Open Questions

None at this time, unless you have specific additional datasets you'd like included beyond Portfolio, Active Follows, and Macro Regime.

## Proposed Changes

### `scripts/export_notebooklm.py`

#### [NEW] `scripts/export_notebooklm.py`
A new script that pulls data from the core system components and constructs a Markdown document:
- **Initialization**: Will call `_bootstrap.bootstrap()` to correctly load `.env` configurations.
- **Portfolio Data**: Uses `HistoricalStore(readonly=True).latest_account_snapshot()` to get current holdings, buying power, and total equity.
- **Followed Pilots**: Uses `FollowsStore().list_active()` to get active strategy follows.
- **Macro Data**: Retrieves the latest macroeconomic variables (VIX, Sahm rule, HY OAS, etc.) using `HistoricalStore(readonly=True).get_macro()` and formats them into a "Macro Context" section.
- **Honesty Invariant (CONSTRAINT #4)**: Missing data will strictly be formatted as `"N/A"` or `"Unknown"` and never coerced to `0` or fabricated values.

### `cli_introspect/targets.py` (Optional)

#### [MODIFY] `cli_introspect/targets.py`
We can add this new script to the `TARGETS` list so it can be run as a Background Job directly from the Pilots PWA Commands screen.

## Verification Plan

### Automated Tests
- Run `make verify` and test the script execution using `uv run python scripts/export_notebooklm.py`.
- Assert that the output Markdown file exists in `output/notebooklm_source.md`.
- `tests/test_export_notebooklm.py` — happy path, per-section degraded paths, and honest-zero-vs-N/A coverage.

### Manual Verification
- Manually review the generated `output/notebooklm_source.md` to ensure it formats nulls/NaNs correctly as "N/A" and reads as a clean document.

## Documentation Update Step

Checked `docs/README.md`, `docs/architecture/webapp-and-gui.md`, `docs/HOW_TO_GUIDE.md`, `CLAUDE.md`, and `AGENTS.md`. **No doc file needs a change for this script.** Reasoning: comparably-sized, single-purpose `scripts/*.py` entry points already in `cli_introspect/targets.py` — `daily_briefing.py`, `track_record_status.py`, `repair_price_bars_adjustment.py` — have no dedicated documentation anywhere in `docs/architecture/`; they're only named in passing where directly relevant to another writeup. This script (no new setting, no behavior change to an existing subsystem, no bug fix) fits that same no-dedicated-bullet tier. `docs/README.md` only indexes doc files, not individual scripts, so it's unaffected either way. This conclusion was reached and stated explicitly per CLAUDE.md's requirement that every Implementation Plan include this step even when the answer is "nothing to change."

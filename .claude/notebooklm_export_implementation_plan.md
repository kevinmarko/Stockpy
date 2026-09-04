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

### Manual Verification
- Manually review the generated `output/notebooklm_source.md` to ensure it formats nulls/NaNs correctly as "N/A" and reads as a clean document.

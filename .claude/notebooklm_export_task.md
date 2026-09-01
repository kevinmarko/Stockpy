# NotebookLM Export Task Tracker

- [x] Identify requirements for NotebookLM pipeline (needs to be Markdown, not JSON).
- [x] Investigate current platform components (`HistoricalStore`, `MacroEconomicDTO`, `Pilots`).
- [x] Propose plan to build a backend script (`scripts/export_notebooklm.py`).
- [x] Create `scripts/export_notebooklm.py` with standard repo imports and proper missing data fallback (`CONSTRAINT #4`).
- [x] Add script to `cli_introspect/targets.py`.
- [x] Rebuild `command_manifest.json` so UI commands panel can invoke it.
- [x] Test the script locally and verify `output/notebooklm_source.md` outputs expected Markdown.
- [x] Ensure `argparse` is added so `cli_introspect` properly ingests it.
- [x] Re-run `build_command_manifest.py` and verify `0 dead-letter(s)`.
- [x] Write `walkthrough.md`.

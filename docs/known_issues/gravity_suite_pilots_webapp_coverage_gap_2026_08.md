# Gravity Suite Pilots/Webapp Coverage Gap (August 2026)

The repository's structural and LLM audit suites have a measured, systemic coverage gap regarding the `pilots/` and `webapp/` surfaces, despite those surfaces being the platform's single largest source of real, found bugs (empirically ~8-9 of 44 known-issues entries, including a documented 21-bug parity sweep).

**Current Coverage:**
- Structural Gravity AI Review Suite (`Gravity AI Review Suite.py`): 1 of 94 steps covers the webapp.
- LLM Auditor (`engine/gravity_ai_runner.py`): 0 of 8 steps cover the webapp.

**Resolution Plan:**
This gap is being addressed in the upcoming scheduled audit pass (Gaps 1-3).
- **Gap 1**: Mechanized parity and fabrication-pattern audit for `pilots/` and `webapp/`.
- **Gap 2**: LLM-audit MCP-tool gap and `pilots/webapp` coverage for the real LLM auditor.
- **Gap 3**: Cadence-verification for data-pipeline and math/validation cron jobs.

Note: The root-level `Gravity_Verification_Report.json` (an old grader-shape fossil from 2026-06) is a known candidate for deletion, as its format is mismatched with the current 90-step suite.

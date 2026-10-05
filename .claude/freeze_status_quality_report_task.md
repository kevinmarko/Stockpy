# freeze_status_quality_report - task tracker

- [x] Plan (`.claude/freeze_status_quality_report_implementation_plan.md`)
- [x] Quality block in `scripts/feature_freeze_status.py` (hold times, close reasons, entry dates, open P&L, sector concentration, `--live-quotes`)
- [x] Gate and exit code unchanged; quality computed in its own try/except
- [x] Tests in `tests/test_feature_freeze_status.py`
- [x] Docs: `docs/RUNBOOK.md` 3.16, step-7 walkthrough pointer (CLAUDE.md/AGENTS.md deliberately untouched per coordinator)
- [x] Targeted pytest, ruff F821/F822/F823/E9, `make ci`
- [x] Live read-only run against the real DB
- [ ] PR review and merge (operator)

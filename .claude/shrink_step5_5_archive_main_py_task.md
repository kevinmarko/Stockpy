# Step 5.5 task tracker

- [x] Plan approved (operator 2026-10-12); merge held until primary soak through 2026-10-16
- [x] `shared/market_hours.py`; git mv main.py, pipeline/steps.py, html_publisher, trade_signals, advisory_agent, daily-advisory plist
- [x] `reporting/state_snapshot.py` trimmed to orchestrator helpers; advisory writer → legacy
- [x] Importers repointed; settings/.env.example queue-mode text; installer flag removed
- [x] Tests moved/retargeted; new import-smoke gate (+ mutation check)
- [x] Gravity retargeted (incl. step 30)
- [x] Docs, skills, CODEOWNERS, CLAUDE.md/AGENTS.md, FEATURE_TIER_HISTORY, legacy/README.md
- [x] Census/liveness regenerated
- [ ] `make ci` green on the final tree
- [ ] After 2026-10-16: live daemon healthy (GET /status, recent runs succeeded, queue fresh) → merge; sync main checkout

# Step 5.6 task tracker

- [x] Plan approved (operator, 2026-10-12); branch stacked on `step5-5-archive-main-py`, merges after #1124
- [x] `launch_orchestrator` fast path unconditional (subprocess fallback kept); `launch_daemon_engine` + `DAEMON_LOG_PATH` removed
- [x] `launch_webapp.command` always starts the daemon when :8601/:8602 are free
- [x] Field removed from `settings.py`, `env_io.ALLOWED_KEYS`, pilots_api tunables, `.env.example`, webapp mocks; passing mentions reworded
- [x] Root-conftest fixture `_isolate_daemon_control_api_in_tests` (closed Control API port in tests)
- [x] Tests retargeted; guard that the flag can't come back
- [x] Census/liveness regenerated
- [x] Docs: CLAUDE.md/AGENTS.md, HOW_TO_GUIDE, RUNBOOK, architecture (webapp-and-gui, data-layer), FEATURE_TIER_HISTORY, parent plan status
- [x] `make ci` green on the final tree (11,892 passed, 0 failed); webapp typecheck + vitest 1,814 passed; mock-mode browser check
- [ ] After #1124 merges: retarget this PR to `main`, rebase, re-verify, merge, sync main checkout

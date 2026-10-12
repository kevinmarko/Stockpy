# Step 5.4a task tracker: runtime callers off `main.py`

Plan: `.claude/shrink_step5_4_callers_implementation_plan.md` (PR 5.4a section). Walkthrough:
`.claude/shrink_step5_4a_callers_runtime_walkthrough.md`.

- [x] Step 0: build `.venv` (uv, not `setup.sh`: it needs zsh) and `node_modules`; green baseline (626 Python, 82 webapp)
- [x] `shared/orchestrator_runner.py`: delete `launch_advisory_main`, `launch_scheduled_advisory`, `launch_symbol_retry`, the three log-path constants and `HIGH_STAKES_COMMANDS["main.py"]`; fix the `--refresh-account` passthrough; add daemon-only `launch_daemon_cycle()`
- [x] `api/_jobs.py`: `JobType.ADVISORY` -> `launch_daemon_cycle()`; reject `refresh_account` (400)
- [x] `POST /dead-letter/retry` -> one full daemon cycle (`{symbol, run_id, applies, note}`)
- [x] MCP `generate_html_report` -> daemon cycle + freshness check on `daily_report_dashboard.html`
- [x] `main.py` out of the command manifest, completions, webapp mock manifest and `commandParse.ts`
- [x] Operator-facing "run `python3 main.py`" hints replaced (Python + webapp); stale settings descriptions fixed
- [x] Gravity suite checks for the deleted launchers updated
- [x] `shared/daemon_client.trigger_run`: unwrap FastAPI's nested error `detail` (409 run id / 423 reason were always lost); tolerate non-object JSON
- [x] API-parity review + honesty audit; fixes for V1-V4, P1-P3, createJob 409 mislabel, mock refusal paths
- [x] Settings census + liveness regenerated
- [x] Full offline suite (11,898 passed; 2 failures explained in the walkthrough), webapp typecheck + vitest on the final tree
- [x] Browser pass (Playwright) and real-daemon smoke in mock mode
- [ ] Commit, push, draft PR, subscribe

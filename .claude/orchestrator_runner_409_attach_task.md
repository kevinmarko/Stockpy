# Task: orchestrator_runner 409 attach

- [x] Branch `orchestrator-runner-409-attach` from `origin/main` (#1124/#1125 held until after 2026-10-16)
- [x] `launch_orchestrator`: 409 `already_running` attaches (body id, else verified `GET /run/latest`)
- [x] Refuse (RuntimeError, raised outside the fast-path catch-all) when the in-flight run is a non-full sub-run
- [x] Keep subprocess fallback: unreachable daemon, other trigger errors, 409 with no verifiable id, exceptions, `dry_run=True`
- [x] Review caller `api/_jobs.py`: no change needed
- [x] Tests in `tests/test_orchestrator_runner_daemon_cutover.py`
- [x] Docs: `docs/architecture/webapp-and-gui.md`
- [x] Targeted tests (228 passed) + `make ci`
- [ ] PR; note the rebase conflict for #1125

# Runtime Bug Hunter Fixes — Task Tracker

- [x] Fix `conftest.py` settings singleton isolation (`Settings(_env_file=None)`) <!-- id: 1 -->
- [x] Fix `scripts/preflight_check.py` `check_db_exists` canonical database path resolution <!-- id: 2 -->
- [x] Fix `universe_engine.py` Wikipedia scraper pandas deprecation & exception contracts <!-- id: 3 -->
- [x] Fix `validation/metrics.py` and `validation/autonomous_backtest_runner.py` NaN Sharpe handling <!-- id: 4 -->
- [x] Declare `NO_VENV_REEXEC` in `settings.py` and `.env.example` <!-- id: 5 -->
- [x] Add missing docstrings flagged by AST auditor <!-- id: 6 -->
- [x] Update documentation (`CLAUDE.md`, `AGENTS.md`, `docs/incident_log.md`) <!-- id: 7 -->
- [x] Run test suite and Bug Hunter validation <!-- id: 8 -->
- [ ] Commit and open Pull Request <!-- id: 9 -->

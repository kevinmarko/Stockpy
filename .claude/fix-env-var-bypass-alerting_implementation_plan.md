# Alerting Subsystem Fixes

## Overview
Implement fixes to the alerting subsystem to bypass `os.getenv` bypassing.
1. `alerting.py`: Fixed `setup_logging` to use `settings.LOG_LEVEL`.
2. `alerting.py`: Fixed `notify` to return a `bool`, reference `ALERT_NTFY_TOPIC` via `settings`, and updated docstrings.
3. `alerting_mcp/notifier.py`: Migrated 6 `os.getenv` email and Slack configuration fields (and 2 others) to use the `settings` singleton and added the missing import `from settings import settings`.
4. Fixed test files `tests/test_alerting.py` and `tests/test_alerting_mcp_notifier.py` to use `monkeypatch.setattr` on the `settings` singleton instead of `monkeypatch.setenv`.

## Verification
- Run `pytest tests/test_alerting.py tests/test_alerting_mcp_notifier.py tests/test_alerts.py` to ensure all tests pass.

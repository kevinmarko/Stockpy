# Implementation Plan: Sentry Daemon Instrumentation

1.  **Create `observability/sentry_integration.py`**
    *   Implement the `init_sentry(*, service_name: str) -> bool` function.
    *   Use lazy import for `sentry_sdk` and gracefully return `False` if missing (logs a WARNING).
    *   Read `SENTRY_ENABLED`, `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE` from `settings`.
    *   Ensure `send_default_pii=False` is passed to `sentry_sdk.init`.
    *   Wrap `sentry_sdk.init` in `try...except Exception` to prevent it from crashing the daemon on failure (logs WARNING).
    *   Return `True` on success.

2.  **Verify `observability/sentry_integration.py`**
    *   Read the file after creation to ensure everything is correct.

3.  **Update `settings.py`**
    *   Add `SENTRY_ENABLED` (default `True`), `SENTRY_DSN` (default `None`), `SENTRY_ENVIRONMENT` (default `"development"`), and `SENTRY_TRACES_SAMPLE_RATE` (default `0.0`) to the `Settings` class. Include descriptive `Field` comments, placed near `ALERT_WEBHOOK_URL`.

4.  **Update `desktop/orchestrator_daemon.py`**
    *   In `run_forever()`, add a deferred import for `init_sentry` from `observability.sentry_integration`.
    *   Call `init_sentry(service_name="orchestrator_daemon")` right after `_load_dotenv(ENV_PATH, override=False)` and the `signal.pthread_sigmask` call.

5.  **Update `requirements-optional.txt`**
    *   Add `sentry-sdk` (e.g., `>=2.0.0`) with a header comment explaining its purpose and the graceful degradation pattern.

6.  **Update `env_io.py`**
    *   Add `SENTRY_ENABLED`, `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE` to `ALLOWED_KEYS`.
    *   Add `SENTRY_DSN` to `SECRET_KEYS`.

7.  **Verify settings changes**
    *   Run a simple python script in `.venv` to import `settings` and `env_io` to ensure no syntax/import errors.

8.  **Create tests in `tests/test_sentry_integration.py`**
    *   Add unit tests for `init_sentry` to verify the various conditions: disabled, missing DSN, missing `sentry_sdk`, init raises exception, and success.

9.  **Update `tests/test_orchestrator_daemon.py`**
    *   Add a mock/patch to verify that `run_forever()` correctly invokes `init_sentry(service_name="orchestrator_daemon")` during initialization.

10. **Update documentation**
    *   Update `CLAUDE.md` to describe the new Sentry feature.
    *   Update `docs/architecture/observability-and-apis.md` to mention `observability/sentry_integration.py`.
    *   Update `docs/architecture/webapp-and-gui.md` to mention the daemon entrypoint calling `init_sentry()`.

11. **Submit PR artifacts**
    *   Write this implementation plan.
    *   Write the task description.
    *   Write the walkthrough.

12. **Verify PR artifacts**
    *   Run `ls -l .claude/` to ensure they exist.

13. **Run tests**
    *   Run `pytest tests/test_sentry_integration.py tests/test_orchestrator_daemon.py` and `make verify` to ensure the changes are correct and haven't broken the pipeline.

14. **Complete pre-commit steps**
    *   Check for any specific pre-commit hooks or scripts necessary.

15. **Submit PR**
    *   Commit changes and submit the pull request.
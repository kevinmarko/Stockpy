# Walkthrough: Sentry Daemon Instrumentation

This pull request introduces Sentry error-tracking instrumentation into the persistent background orchestrator daemon. This feature helps capture unhandled exceptions that occur during unattended operations where failures might otherwise go unnoticed.

## Summary of Changes

*   **New Module: `observability/sentry_integration.py`**
    *   Created `init_sentry(*, service_name: str) -> bool` to encapsulate the Sentry initialization process.
    *   The implementation uses lazy loading for the `sentry_sdk` module, adhering to the project's graceful degradation pattern for optional dependencies (like TensorFlow and FinBERT).
    *   If Sentry is disabled, the DSN is missing, the SDK is not installed, or initialization fails, the function logs a warning and returns `False` without raising an exception that could interrupt the daemon's startup.
*   **Settings Updates (`settings.py`, `env_io.py`)**
    *   Added `SENTRY_ENABLED` (default `True`), `SENTRY_DSN` (default `None`), `SENTRY_ENVIRONMENT` (default `"development"`), and `SENTRY_TRACES_SAMPLE_RATE` (default `0.0`) to the main configuration.
    *   Added the public fields to `ALLOWED_KEYS` for GUI access and added `SENTRY_DSN` to `SECRET_KEYS` in `env_io.py` to ensure it is handled securely.
*   **Daemon Wiring (`desktop/orchestrator_daemon.py`)**
    *   Integrated the `init_sentry` call directly into `run_forever()`, specifically placed after `_load_dotenv` and `signal.pthread_sigmask` configuration for correct execution order.
*   **Optional Dependencies (`requirements-optional.txt`)**
    *   Added `sentry-sdk` to the optional requirements with a descriptive block explaining its role and the graceful fallback behavior.
*   **Tests**
    *   Created `tests/test_sentry_integration.py` to unit test the initialization function under all supported states (missing deps, success, failure, disabled).
    *   Updated `tests/test_orchestrator_daemon.py` with mock assertions to confirm the daemon entrypoint triggers the Sentry integration properly during its lifecycle.
*   **Documentation**
    *   Added specific Sentry instrumentation entries to `CLAUDE.md`, `docs/architecture/observability-and-apis.md`, and `docs/architecture/webapp-and-gui.md` to reflect the architectural enhancement and scope constraints.
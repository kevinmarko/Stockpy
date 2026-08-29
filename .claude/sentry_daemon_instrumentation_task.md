# Task Description: Sentry Daemon Instrumentation

Add Sentry error-tracking instrumentation to the persistent orchestrator daemon entrypoint.

CONTEXT
This is InvestYo/Stockpy, a quant trading platform. `desktop/orchestrator_daemon.py` (entrypoint) + `desktop/daemon_runtime.py` (OrchestratorDaemon class) implement a persistent background daemon that is the one process meant to run genuinely unattended. Unlike on-demand CLI entrypoints where a crash is visible in the launching terminal, this daemon can fail silently for hours. Wire Sentry into it so unhandled exceptions are reported automatically.

SCOPE: the daemon entrypoint only. Do NOT touch main.py or the standalone api/*.py services in this change.

1. NEW MODULE: observability/sentry_integration.py
   Sibling to the existing observability/alerts.py. Expose `init_sentry(*, service_name: str) -> bool`. Use lazy imports for `sentry_sdk`, graceful fallbacks on missing configurations or packages, and handle exceptions to avoid crashing daemon startup.
2. NEW SETTINGS in settings.py: Add `SENTRY_ENABLED` (default True), `SENTRY_DSN`, `SENTRY_ENVIRONMENT` (default "development"), and `SENTRY_TRACES_SAMPLE_RATE` (default 0.0) near `ALERT_WEBHOOK_URL`.
3. WIRING: In `desktop/orchestrator_daemon.py::run_forever()`, call `init_sentry(service_name="orchestrator_daemon")` right after environment load and sigmask setup.
4. DEPENDENCIES: Add `sentry-sdk` to `requirements-optional.txt` with an explanatory comment mirroring the ML optional dependencies.
5. ENV IO: Update `env_io.py` adding non-secret settings to `ALLOWED_KEYS` and the DSN to `SECRET_KEYS`.
6. TESTS: Add `tests/test_sentry_integration.py` for testing `init_sentry`, and update `tests/test_orchestrator_daemon.py` to assert the hook is called during daemon start.
7. DOCS: Update `CLAUDE.md`, `docs/architecture/observability-and-apis.md`, and `docs/architecture/webapp-and-gui.md`.
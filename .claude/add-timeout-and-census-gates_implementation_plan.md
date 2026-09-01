# Work Package E Implementation Plan

## §0 Dependency Check
- `scripts/measure_settings_census.py` and `tests/test_measure_settings_census.py` exist.
- Schema components align with instructions. 

## E1: test_measure_settings_census.py
Add a new test inside `TestCommittedArtifactIsFresh` to verify the exact keys present in `fresh_census["read_forms"]["form_d_os_environ"]["counts"]`. 
The required keys are a temporary allowlist combining pending WP A-D fields:
`FUNDAMENTALS_CACHE_TTL_SECONDS`, `FUNDAMENTALS_NEG_CACHE_TTL_SECONDS`, `FINNHUB_RATE_LIMIT_PER_MIN`, `WATCHLIST`, `LOG_LEVEL`, `NTFY_TOPIC`, `ALERT_NTFY_TOPIC`, `ALERT_EMAIL_SMTP_HOST`, `ALERT_EMAIL_SMTP_PORT`, `ALERT_EMAIL_SMTP_PASSWORD`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO`, `ALERT_SLACK_WEBHOOK_URL`, `ALERT_CHANNELS`, `PROMPT_REGISTRY_SIGNING_KEY`, `QDRANT_URL`, `QDRANT_COLLECTION`
and the valid ones: `GCLOUD_BIN`, `NO_VENV_REEXEC`.
Include an inline comment stating they will be "removed once WP-A/B/C/D lands", and the justification words from `scripts/auditor/stockpy_codebase_auditor.py` for benign justifications (HOME, PATH, PWD, USER, TERM, HTTPS_PROXY, HTTP_PROXY, DATABASE_URL, PYTEST_CURRENT_TEST, CI, RH_LOGIN_WORKER, NO_VENV_REEXEC).

## E2: test_no_missing_call_timeouts.py
Create an AST guard in `tests/test_no_missing_call_timeouts.py` to flag any `subprocess.run`, `subprocess.call`, `subprocess.check_call`, `subprocess.check_output` and `requests.<method>` calls missing a `timeout=` keyword. 
Do NOT flag `subprocess.Popen(...)` or `.wait()`, and disclose this explicitly as a known un-covered gap in the test's docstring. 
Allowlist `main.py` and `main_orchestrator.py`'s `subprocess.call([venv_python] + sys.argv)` venv re-exec pattern.

## Verification
Run `pytest tests/test_measure_settings_census.py tests/test_no_missing_call_timeouts.py -q`
Make sure tests pass. Do not run `python3 scripts/measure_settings_census.py --write` yet.

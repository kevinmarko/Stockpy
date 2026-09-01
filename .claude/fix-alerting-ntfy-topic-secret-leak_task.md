# Task tracker: fix-alerting-ntfy-topic-secret-leak

- [x] Confirm `ALERT_NTFY_TOPIC` is a real `SECRET_KEYS` field
- [x] Confirm the leak is still present on current `main` (re-fetched, re-checked before fixing)
- [x] Fix `alerting.py::notify()` to drop `topic` from the non-2xx log call
- [x] Strengthen `tests/test_alerting.py::TestNotify::test_non_2xx_http_status_logged_not_raised`
      to assert the secret value never appears in log output
- [x] Prove the regression-catching property (revert fix -> test fails with leak visible;
      restore -> passes)
- [x] Run full local test suite for touched modules: 97 passed, 0 failed
- [x] Commit with descriptive message
- [ ] Push branch and open PR (blocked on working `gh` auth in the authoring session --
      branch pushed directly via `git push`; PR creation left to a session/user with
      working `gh`)

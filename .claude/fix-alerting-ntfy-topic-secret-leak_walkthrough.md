# Walkthrough: fix-alerting-ntfy-topic-secret-leak

## What changed

One file, one behavior fix: `alerting.py::notify()`'s non-2xx-response warning log no
longer includes the ntfy topic string. Previously:

```python
logger.warning(
    "ntfy POST returned unexpected HTTP %d for topic '%s'.",
    resp.status,
    topic,
)
```

Now:

```python
logger.warning(
    "ntfy POST returned unexpected HTTP %d for the configured topic.",
    resp.status,
)
```

## Why

`settings.ALERT_NTFY_TOPIC` is classified as a `SECRET_KEYS` field in `env_io.py` --
ntfy.sh has no separate auth token, so the topic name itself is the access-control
mechanism (this is stated explicitly in `notify()`'s own docstring: "ntfy.sh topics are
access-controlled by the topic name alone (keep the topic unguessable...)"). Logging it
in cleartext on every non-2xx response defeats that "keep it unguessable" premise the
moment `{LOCAL_DATA_ROOT}/logs/investyo.log` is read by anyone/anything other than the
operator (log aggregation, a support request, a shared debugging session).

This was found during an independent 10-agent audit of PRs #974-979 (a since-superseded,
redundant re-implementation of work already merged via PR #962/#969). That audit's
cross-cutting CONSTRAINT #4/#3 pass specifically re-checked `alerting.py::notify()` --
since PR #962/#969's own prior fix pass touched this exact function (correcting the
`NTFY_TOPIC` -> `ALERT_NTFY_TOPIC` variable-name bug) but left this cleartext-logging
line untouched -- and confirmed the leak is real and still present on current `main`.

## How it was verified

1. Re-fetched `main` to the true latest tip (`9d1e8c27` at time of fixing) and re-confirmed
   the bug was still present there before writing the fix -- not fixing against stale code.
2. Applied the fix.
3. Strengthened the one existing test that exercises this exact path
   (`test_non_2xx_http_status_logged_not_raised`) to use a secret-shaped topic value and
   assert it's absent from `caplog.text`.
4. Proved the test is a real regression guard, not decorative: reverted the fix locally,
   re-ran the test, watched it fail with the leaked topic visible in the assertion's own
   diff output, restored the fix, re-ran green.
5. Ran the full touched-module test set: `pytest tests/test_alerting.py
   tests/test_alerting_mcp_notifier.py tests/test_gui_env_io.py -q` -- 97 passed, 0 failed.

## What a reviewer should know

- This is a pure logging-hygiene fix -- no behavior change to `notify()`'s return value,
  retry logic, or caller-visible contract. Every existing caller is unaffected.
- Scope is deliberately narrow: only this one log line. No other module was found logging
  `ALERT_NTFY_TOPIC` in cleartext during the audit that surfaced this
  (`alerting_mcp/notifier.py::_send_ntfy`'s own failure path was checked and is already clean).
- `gh` was unavailable in the authoring session (broken keyring token), so this branch was
  pushed directly via `git push` rather than through `gh pr create`. If this file is present
  without a corresponding open PR, that's why -- open one manually from this branch against
  `main`.

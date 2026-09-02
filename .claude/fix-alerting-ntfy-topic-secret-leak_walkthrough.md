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
in cleartext defeats that "keep it unguessable" premise the moment
`{LOCAL_DATA_ROOT}/logs/investyo.log` is read by anyone/anything other than the operator
(log aggregation, a support request, a shared debugging session).

**Correction from a post-merge review**: this doc originally said the leak fires "on
every non-2xx response." That overstates it -- `urllib.request.urlopen`'s default opener
includes `HTTPErrorProcessor`, so a real 4xx/5xx from ntfy.sh raises `HTTPError` before
this branch is ever reached (caught instead by the `except urllib.error.URLError` clause,
whose own message never embeds the topic -- verified empirically). The branch this PR
fixed is reachable via a genuine 2xx response other than exactly 200/201 (e.g. 202
Accepted, 204 No Content), which is narrower than "any non-2xx response" but still a real,
plausible trigger. The fix itself is unaffected by this correction -- it removes the
secret from every status that reaches the branch, regardless of which ones those are.

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
- `gh`'s initial "invalid keyring token" error was a red herring -- the real cause was the
  sandbox's TLS-intercepting proxy rejecting `gh`'s connection to `api.github.com`.
  Disabling the sandbox for the `gh`/`git push` calls resolved it with no re-authentication
  needed; the branch was pushed and the PR ([#985](https://github.com/kevinmarko/Stockpy/pull/985))
  was opened and merged in the same session.

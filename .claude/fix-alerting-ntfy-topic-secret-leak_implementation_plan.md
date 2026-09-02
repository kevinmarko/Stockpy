# Fix: ALERT_NTFY_TOPIC cleartext secret leak in alerting.py::notify()

## §0 Dependency check

- `settings.ALERT_NTFY_TOPIC` exists as a real `Settings` field (confirmed via `settings.py`)
  and is classified in `env_io.py`'s `SECRET_KEYS` tuple (confirmed via grep).
- `alerting.py::notify()` already correctly reads `settings.ALERT_NTFY_TOPIC` (a prior
  PR #962/#969 audit pass already fixed the earlier `NTFY_TOPIC`-vs-`ALERT_NTFY_TOPIC`
  bypass bug on this exact line) — this fix is narrower: the value, once correctly read,
  was still being logged in cleartext on a non-2xx ntfy response.
- No other module reads/logs this field the same way; `alerting_mcp/notifier.py`'s own
  `_send_ntfy` does not log the topic at all on failure, only a generic message.

## Problem

`alerting.py::notify()`'s non-2xx branch logs:
```python
logger.warning(
    "ntfy POST returned unexpected HTTP %d for topic '%s'.",
    resp.status,
    topic,
)
```
`topic` is `settings.ALERT_NTFY_TOPIC` — a `SECRET_KEYS` field that functions like a
bearer token (ntfy.sh access-controls a topic by its name alone, per this function's own
docstring). This writes the operator's real topic to `{LOCAL_DATA_ROOT}/logs/investyo.log`
in plaintext whenever `resp.status` reaches this branch — which, per a post-merge review,
is narrower than "any non-2xx response": `urllib.request.urlopen`'s default opener includes
`HTTPErrorProcessor`, so a real 4xx/5xx from ntfy.sh raises `HTTPError` before this code
ever runs (caught instead by the `except urllib.error.URLError` clause a few lines below,
confirmed to never embed the topic in its own message). The reachable trigger is a
genuine 2xx response other than exactly 200/201 (e.g. 202 Accepted, 204 No Content) —
still a real, plausible response ntfy.sh could send, just not "any non-2xx response." The
fix removes the leak for every status that reaches this branch regardless, so this
correction doesn't change the fix's validity, only the accuracy of how it's described.
Found during an independent audit pass (2026-09-01) cross-checking PR #962/#969's own
already-merged fix for this file; confirmed still present on the current `main` tip at
time of writing.

## Fix

Drop `topic` from the log call entirely — the HTTP status code is sufficient
operator-facing signal; the operator already knows their own configured topic.

## Test

Strengthened the existing `tests/test_alerting.py::TestNotify::test_non_2xx_http_status_logged_not_raised`
to use a secret-shaped topic value (`"my-secret-topic"`, matching the sibling
`test_successful_post_hits_ntfy_sh_with_topic_in_url`'s convention) and assert it never
appears in `caplog.text`. Regression-catching property proven directly: reverted the fix
locally, confirmed the strengthened test fails with the leaked string visible in the
assertion diff, restored the fix, confirmed green.

## Documentation update

None required beyond this PR-artifact set — this is a narrow logging-hygiene fix with no
new setting, no new behavior an operator configures, and no architecture change. Not
adding a CLAUDE.md bullet since the existing `ALERT_NTFY_TOPIC`/`NTFY_TOPIC` migration
bullet already documents the field's secret-handling contract; this fix closes a gap in
upholding that contract rather than changing it.

## Verification run

```
$ pytest tests/test_alerting.py tests/test_alerting_mcp_notifier.py tests/test_gui_env_io.py -q
97 passed in 3.68s
```

## Agent handoff notes

Branch `fix-alerting-ntfy-topic-secret-leak`, based on `main` at `9d1e8c27`. Opened as
[#985](https://github.com/kevinmarko/Stockpy/pull/985) and merged in the same session,
once `gh`'s "invalid keyring token" error was diagnosed as a sandbox TLS-proxy issue
rather than an actual auth failure (see the task tracker's last item for detail). No
other files touched.

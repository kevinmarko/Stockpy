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
docstring). Any non-2xx ntfy response writes the operator's real topic to
`{LOCAL_DATA_ROOT}/logs/investyo.log` in plaintext. Found during an independent audit
pass (2026-09-01) cross-checking PR #962/#969's own already-merged fix for this file;
confirmed still present on the current `main` tip at time of writing.

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

Branch `fix-alerting-ntfy-topic-secret-leak`, based on `main` at `9d1e8c27`. Pushed
directly (no `gh` available in the authoring session — `gh auth` was broken; PR opened
manually or via a follow-up session with working `gh`). No other files touched.

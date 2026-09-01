# Goal Description
Fix env var bypass bugs for alerting related variables.

## §0 Dependency check
Confirm that `settings.settings` modules exist and have the variables we need.

## Proposed Changes
### alerting.py
- Bug 1: `setup_logging`: Change `os.environ.get("LOG_LEVEL", log_level).upper()` to `(settings.settings.LOG_LEVEL or log_level).upper()`.
- Bug 2: `notify`: Change `os.environ.get("NTFY_TOPIC", ...)` to `settings.settings.ALERT_NTFY_TOPIC`.

### gui/robinhood_execution_panel.py
- Bug 2: `ntfy_topic_configured`: Change `os.environ.get("NTFY_TOPIC", ...)` to `settings.settings.ALERT_NTFY_TOPIC`.

### alerting_mcp/notifier.py
- Bug 3: Swap 6 fields (`ALERT_EMAIL_SMTP_HOST`, `ALERT_EMAIL_SMTP_PORT`, `ALERT_EMAIL_SMTP_PASSWORD`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO`, `ALERT_SLACK_WEBHOOK_URL`, `ALERT_CHANNELS`) to `settings.settings.X`.

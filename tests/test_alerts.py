"""
tests/test_alerts.py
====================
Unit tests for ``observability/alerts.py``.

Testing strategy
----------------
Every channel is tested in *isolation* by:
  1. Constructing a minimal ``MagicMock`` settings object that configures only
     the channel under test.  This avoids accidental cross-channel dispatch
     (e.g. a Discord URL configured globally causing Discord calls in a Slack
     test).
  2. Patching the external I/O boundary (``urllib.request.urlopen``,
     ``smtplib.SMTP``) with lightweight in-process fakes rather than real
     network calls.  This keeps the tests fast, deterministic, and offline.

Coverage
--------
* Console channel: fires at the correct Python log level per alert severity.
* File channel: writes well-formed JSON-lines; appends on subsequent calls;
  includes ``extra`` fields in the record.
* Discord webhook: correct ``{"content": "..."}`` payload; ``Content-Type:
  application/json`` header present; ``URLError`` is swallowed (never raises).
* Slack webhook: correct ``{"text": "..."}`` payload; emoji prefix present.
* Email (SMTP): ``sendmail()`` called with correct sender and all recipients;
  ``Subject:`` header contains the alert level.
* ``send_daily_summary``: delegates to ``send_alert``; P&L formatted as USD;
  warning list included; "no closed trades" fallback text when empty.
* Unconfigured channel: requesting a channel whose setting is ``None`` or empty
  is silently skipped — no exception, no call.
"""

from __future__ import annotations

import json
import smtplib
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch as _patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(tmp_path: Path, **overrides) -> Any:
    """Build a MagicMock that looks like ``settings`` for a single test.

    All alert-related settings default to ``None`` (unconfigured) so that only
    the overrides passed in are active.  This prevents a test for the file
    channel from accidentally also triggering a Discord POST because some
    other test left ``DISCORD_WEBHOOK_URL`` set on the real settings singleton.

    Parameters
    ----------
    tmp_path:
        ``pytest`` fixture-provided temporary directory.  Included as a
        parameter so callers can use ``str(tmp_path / "some_file")`` in
        overrides without having to compute paths themselves.
    **overrides:
        Any settings attribute to set to a non-None value, e.g.
        ``ALERT_FILE_PATH=str(tmp_path / "alerts.jsonl")``.
    """
    m = MagicMock()
    m.DISCORD_WEBHOOK_URL = overrides.get("DISCORD_WEBHOOK_URL", None)
    m.SLACK_WEBHOOK_URL = overrides.get("SLACK_WEBHOOK_URL", None)
    m.ALERT_FILE_PATH = overrides.get("ALERT_FILE_PATH", None)
    m.ALERT_EMAIL_FROM = overrides.get("ALERT_EMAIL_FROM", None)
    m.ALERT_EMAIL_TO = overrides.get("ALERT_EMAIL_TO", None)
    m.ALERT_SMTP_HOST = overrides.get("ALERT_SMTP_HOST", None)
    m.ALERT_SMTP_PORT = overrides.get("ALERT_SMTP_PORT", 587)
    m.ALERT_SMTP_USER = overrides.get("ALERT_SMTP_USER", None)
    m.ALERT_SMTP_PASSWORD = overrides.get("ALERT_SMTP_PASSWORD", None)
    m.ALERT_DEDUP_WINDOW_SECONDS = overrides.get("ALERT_DEDUP_WINDOW_SECONDS", 900)
    return m


@pytest.fixture(autouse=True)
def _reset_dedup_state():
    """Clear in-process alert-dedup state before and after every test.

    Without this, a test earlier in the run that fires ``send_alert(...,
    dedup_key="x")`` could silently suppress a later, unrelated test's alert
    with the same key (module-level dict persists across tests in the same
    process).
    """
    from observability.alerts import reset_dedup_state
    reset_dedup_state()
    yield
    reset_dedup_state()


# ---------------------------------------------------------------------------
# Helpers shared by webhook tests
# ---------------------------------------------------------------------------

class _FakeOkResponse:
    """Context-manager response stub that reports HTTP 200.

    Used as the return value of fake ``urlopen`` implementations.  The
    context-manager protocol (``__enter__`` / ``__exit__``) is required because
    ``_send_discord`` / ``_send_slack`` use ``with urllib.request.urlopen(...)``
    rather than calling the result directly.
    """
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): pass


# ---------------------------------------------------------------------------
# Console channel
# ---------------------------------------------------------------------------

class TestConsoleChannel:
    """The console channel dispatches to Python's logging framework.

    Because ``send_alert`` always includes "console" in its default channel
    list, this is the last-resort audit trail that is always present even when
    all other channels are unconfigured.
    """

    def test_critical_logs_at_critical_level(self, caplog):
        """A CRITICAL alert must produce a CRITICAL log record.

        Mapping alert severity to Python log level lets log aggregators
        (e.g. CloudWatch, Datadog) filter by standard log level rather than
        having to parse the message body for the bracketed ``[CRITICAL]`` tag.
        """
        import logging
        from observability.alerts import send_alert
        with _patch("observability.alerts.settings", _make_settings(Path())):
            with caplog.at_level(logging.CRITICAL, logger="observability.alerts"):
                send_alert("CRITICAL", "Test critical message", channels=["console"])
        assert any("CRITICAL" in r.message for r in caplog.records)

    def test_warning_logs_at_warning_level(self, caplog):
        """A WARNING alert must produce a WARNING log record (not INFO or higher)."""
        import logging
        from observability.alerts import send_alert
        with _patch("observability.alerts.settings", _make_settings(Path())):
            with caplog.at_level(logging.WARNING, logger="observability.alerts"):
                send_alert("WARNING", "Test warning", channels=["console"])
        assert any("WARNING" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# File channel
# ---------------------------------------------------------------------------

class TestFileChannel:
    """The file channel writes append-only JSON-lines to ALERT_FILE_PATH.

    JSON-lines format (one object per line) is chosen over plain logs because
    the preflight check and dashboard can parse it without a log-parsing library.
    """

    def test_writes_json_lines(self, tmp_path: Path):
        """Two ``send_alert`` calls produce exactly two JSON-lines records.

        Each record must contain ``level``, ``message``, and ``timestamp`` keys.
        """
        from observability.alerts import send_alert
        alert_file = tmp_path / "alerts.jsonl"
        s = _make_settings(tmp_path, ALERT_FILE_PATH=str(alert_file))
        with _patch("observability.alerts.settings", s):
            send_alert("INFO", "First alert", channels=["file"])
            send_alert("WARNING", "Second alert", channels=["file"])
        lines = alert_file.read_text().strip().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["level"] == "INFO"
        assert first["message"] == "First alert"
        assert "timestamp" in first

    def test_appends_not_overwrites(self, tmp_path: Path):
        """A second call to ``send_alert`` appends a new line, not overwrites.

        The file is opened in append mode (``"a"``) so historical alert
        records are never lost by a later dispatch.  This is important for
        post-incident review of the alert timeline.
        """
        from observability.alerts import send_alert
        alert_file = tmp_path / "alerts.jsonl"
        s = _make_settings(tmp_path, ALERT_FILE_PATH=str(alert_file))
        # Two separate ``with`` blocks simulate two separate process invocations.
        with _patch("observability.alerts.settings", s):
            send_alert("INFO", "msg1", channels=["file"])
        with _patch("observability.alerts.settings", s):
            send_alert("INFO", "msg2", channels=["file"])
        lines = alert_file.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_extra_fields_included(self, tmp_path: Path):
        """The ``extra`` dict is merged into the top-level JSON record.

        Structured context (e.g. ``drift_symbol``, ``strategy_id``) passed
        via ``extra`` must appear as top-level keys — not nested under an
        ``"extra"`` sub-key — so log consumers can filter by them directly.
        """
        from observability.alerts import send_alert
        alert_file = tmp_path / "alerts.jsonl"
        s = _make_settings(tmp_path, ALERT_FILE_PATH=str(alert_file))
        with _patch("observability.alerts.settings", s):
            send_alert("CRITICAL", "drift", channels=["file"], extra={"drift_symbol": "AAPL"})
        row = json.loads(alert_file.read_text().strip())
        assert row["drift_symbol"] == "AAPL"


# ---------------------------------------------------------------------------
# Discord channel
# ---------------------------------------------------------------------------

class TestDiscordChannel:
    """Discord alerts are posted as ``{"content": "..."}`` JSON to a webhook URL.

    Discord requires ``Content-Type: application/json`` and accepts HTTP 200
    or 204 as success.  We capture the outbound ``urllib.request.Request``
    object to inspect both the payload and headers without making real calls.
    """

    WEBHOOK = "https://discord.com/api/webhooks/fake/url"

    def _settings(self, tmp_path: Path) -> Any:
        """Settings with only the Discord webhook configured."""
        return _make_settings(tmp_path, DISCORD_WEBHOOK_URL=self.WEBHOOK)

    def test_posts_json_payload(self, tmp_path: Path):
        """Payload must be ``{"content": "<emoji> [LEVEL] <ts>\\n<message>"}``.

        Discord's incoming webhook API accepts only a plain-text ``content``
        field for simple notifications; embed support is intentionally omitted
        to keep the implementation simple.
        """
        from observability.alerts import send_alert
        captured: list[bytes] = []

        def fake_urlopen(req, timeout=None):
            captured.append(req.data)
            return _FakeOkResponse()

        with _patch("observability.alerts.settings", self._settings(tmp_path)):
            with _patch("observability.alerts.urllib.request.urlopen", fake_urlopen):
                send_alert("CRITICAL", "Kill switch activated", channels=["discord"])

        assert len(captured) == 1
        payload = json.loads(captured[0].decode())
        assert "content" in payload
        assert "CRITICAL" in payload["content"]
        assert "Kill switch activated" in payload["content"]

    def test_content_type_header(self, tmp_path: Path):
        """Request must carry ``Content-Type: application/json``.

        Discord rejects the payload with HTTP 400 if the content-type header
        is missing or incorrect.
        """
        from observability.alerts import send_alert
        requests_made: list[urllib.request.Request] = []

        def fake_urlopen(req, timeout=None):
            requests_made.append(req)
            return _FakeOkResponse()

        with _patch("observability.alerts.settings", self._settings(tmp_path)):
            with _patch("observability.alerts.urllib.request.urlopen", fake_urlopen):
                send_alert("INFO", "test", channels=["discord"])

        # ``get_header`` capitalises the first letter per HTTP header convention.
        assert requests_made[0].get_header("Content-type") == "application/json"

    def test_http_error_does_not_raise(self, tmp_path: Path):
        """A ``URLError`` from the webhook must be caught and logged, not raised.

        This is the core failure-isolation invariant: a broken webhook URL
        (e.g. deleted webhook, Discord outage) must never propagate an
        exception back to the caller or crash the trading pipeline.
        """
        from observability.alerts import send_alert

        def fail(*a, **kw):
            raise urllib.error.URLError("connection refused")

        with _patch("observability.alerts.settings", self._settings(tmp_path)):
            with _patch("observability.alerts.urllib.request.urlopen", fail):
                send_alert("WARNING", "test", channels=["discord"])  # must not raise


# ---------------------------------------------------------------------------
# Slack channel
# ---------------------------------------------------------------------------

class TestSlackChannel:
    """Slack alerts are posted as ``{"text": "..."}`` JSON to an incoming webhook.

    Slack's payload key is ``"text"`` rather than Discord's ``"content"``;
    this distinction is intentional and load-bearing (the wrong key silently
    produces an empty Slack message).
    """

    WEBHOOK = "https://hooks.slack.com/services/fake/url"

    def _settings(self, tmp_path: Path) -> Any:
        """Settings with only the Slack webhook configured."""
        return _make_settings(tmp_path, SLACK_WEBHOOK_URL=self.WEBHOOK)

    def test_posts_text_payload(self, tmp_path: Path):
        """Payload must be ``{"text": "..."}`` and include the message body."""
        from observability.alerts import send_alert
        captured: list[bytes] = []

        def fake_urlopen(req, timeout=None):
            captured.append(req.data)
            return _FakeOkResponse()

        with _patch("observability.alerts.settings", self._settings(tmp_path)):
            with _patch("observability.alerts.urllib.request.urlopen", fake_urlopen):
                send_alert("WARNING", "Portfolio heat 5.8%", channels=["slack"])

        payload = json.loads(captured[0].decode())
        assert "text" in payload
        assert "Portfolio heat 5.8%" in payload["text"]
        assert "WARNING" in payload["text"]

    def test_emoji_prefix_in_message(self, tmp_path: Path):
        """CRITICAL alerts must include the 🚨 emoji prefix for fast visual triage.

        In a busy Slack channel the emoji is the fastest way to spot a
        CRITICAL alert without reading the bracketed level tag.
        """
        from observability.alerts import send_alert
        captured: list[bytes] = []

        def fake_urlopen(req, timeout=None):
            captured.append(req.data)
            return _FakeOkResponse()

        with _patch("observability.alerts.settings", self._settings(tmp_path)):
            with _patch("observability.alerts.urllib.request.urlopen", fake_urlopen):
                send_alert("CRITICAL", "test", channels=["slack"])

        text = json.loads(captured[0].decode())["text"]
        assert "🚨" in text


# ---------------------------------------------------------------------------
# Email channel
# ---------------------------------------------------------------------------

class TestEmailChannel:
    """Email alerts use ``smtplib.SMTP`` + STARTTLS (port 587).

    ``MockSMTP`` is a lightweight context-manager stub that records what
    ``sendmail()`` was called with so assertions can inspect the outbound
    message without requiring a real SMTP server.
    """

    def _settings(self, tmp_path: Path) -> Any:
        """Settings with a fully-configured SMTP email channel."""
        return _make_settings(
            tmp_path,
            ALERT_EMAIL_FROM="from@example.com",
            ALERT_EMAIL_TO="to1@example.com, to2@example.com",
            ALERT_SMTP_HOST="smtp.example.com",
            ALERT_SMTP_PORT=587,
            ALERT_SMTP_USER="user",
            ALERT_SMTP_PASSWORD="pass",
        )

    def test_sends_email_with_correct_recipients(self, tmp_path: Path):
        """``sendmail()`` must be called with all recipients parsed from the CSV.

        ``ALERT_EMAIL_TO`` is a comma-separated string; the implementation
        splits and strips it so each address is delivered to individually.
        """
        from observability.alerts import send_alert
        sent: list[dict] = []

        class MockSMTP:
            def __init__(self, host, port): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def starttls(self, context=None): pass
            def login(self, u, p): pass
            def sendmail(self, frm, to, msg):
                sent.append({"from": frm, "to": to, "msg": msg})

        with _patch("observability.alerts.settings", self._settings(tmp_path)):
            with _patch("observability.alerts.smtplib.SMTP", MockSMTP):
                with _patch("observability.alerts.ssl.create_default_context", MagicMock()):
                    send_alert("CRITICAL", "Reconciliation drift!", channels=["email"])

        assert len(sent) == 1
        assert sent[0]["from"] == "from@example.com"
        assert "to1@example.com" in sent[0]["to"]
        assert "to2@example.com" in sent[0]["to"]
        assert "Reconciliation drift!" in sent[0]["msg"]

    def test_subject_contains_level(self, tmp_path: Path):
        """The email ``Subject:`` header must include the alert level.

        Operators who scan email subjects (rather than bodies) must be able to
        see the severity without opening the message.
        """
        from observability.alerts import send_alert
        sent_subjects: list[str] = []

        class MockSMTP:
            def __init__(self, host, port): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def starttls(self, context=None): pass
            def login(self, u, p): pass
            def sendmail(self, frm, to, msg):
                for line in msg.splitlines():
                    if line.startswith("Subject:"):
                        sent_subjects.append(line)

        with _patch("observability.alerts.settings", self._settings(tmp_path)):
            with _patch("observability.alerts.smtplib.SMTP", MockSMTP):
                with _patch("observability.alerts.ssl.create_default_context", MagicMock()):
                    send_alert("WARNING", "Heat approaching limit", channels=["email"])

        assert any("WARNING" in s for s in sent_subjects)


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------

class TestDailySummary:
    """``send_daily_summary`` composes a structured message and calls ``send_alert``.

    We patch ``send_alert`` itself so these tests are decoupled from the
    channel dispatch logic (already tested above) and focus purely on the
    message composition.
    """

    def test_summary_calls_send_alert(self, tmp_path: Path):
        """Summary must call ``send_alert("INFO", ...)`` with P&L and warnings."""
        from observability import alerts
        captured: list[tuple] = []

        def fake_send(level, message, channels=None, extra=None):
            captured.append((level, message))

        with _patch("observability.alerts.send_alert", fake_send):
            alerts.send_daily_summary({"main_pipeline": 152.40}, ["Heat reached 5.2%"])

        assert len(captured) == 1
        level, msg = captured[0]
        assert level == "INFO"
        assert "Daily Summary" in msg
        assert "$152.40" in msg
        assert "Heat reached 5.2%" in msg

    def test_summary_no_trades(self, tmp_path: Path):
        """An empty P&L dict must produce an explicit "no closed trades" line.

        Silently omitting the P&L section could be mistaken for a truncated
        message rather than a genuinely quiet day.
        """
        from observability import alerts
        captured: list[tuple] = []

        def fake_send(level, message, channels=None, extra=None):
            captured.append((level, message))

        with _patch("observability.alerts.send_alert", fake_send):
            alerts.send_daily_summary({}, [])

        assert "no closed trades" in captured[0][1]

    def test_unconfigured_channel_skipped(self, tmp_path: Path):
        """Requesting a channel whose URL/path is ``None`` must not raise.

        This test confirms the failure-isolation invariant at the
        ``send_alert`` level: an unconfigured channel produces a ``logger.warning``
        at most — never an exception that propagates to the caller.
        """
        from observability.alerts import send_alert
        # All channels are None / unconfigured in the default _make_settings.
        s = _make_settings(tmp_path)
        with _patch("observability.alerts.settings", s):
            # Requesting all known channels on a settings object that has none
            # of them configured must silently succeed (no exception).
            send_alert("INFO", "test", channels=["discord", "slack", "email", "file"])


# ---------------------------------------------------------------------------
# Dedup / rate-limiting (Phase O2)
# ---------------------------------------------------------------------------

class TestDedup:
    """``send_alert(..., dedup_key=...)`` suppresses repeat alerts within a
    TTL window (``settings.ALERT_DEDUP_WINDOW_SECONDS``), and is purely
    additive: omitting ``dedup_key`` reproduces the pre-dedup always-fires
    behavior exactly (covered by every other test class in this file, none
    of which pass ``dedup_key``).
    """

    def _counting_settings(self, tmp_path: Path, **overrides) -> Any:
        return _make_settings(tmp_path, **overrides)

    def test_same_key_within_window_is_suppressed(self, tmp_path: Path):
        """A second call with the same dedup_key inside the window dispatches nowhere."""
        from observability.alerts import send_alert
        calls: list[str] = []

        def fake_console(level, ts, message):
            calls.append(message)

        s = self._counting_settings(tmp_path, ALERT_DEDUP_WINDOW_SECONDS=900)
        with _patch("observability.alerts.settings", s):
            with _patch("observability.alerts._send_console", fake_console):
                send_alert("WARNING", "first", channels=["console"], dedup_key="heat_AAPL")
                send_alert("WARNING", "second", channels=["console"], dedup_key="heat_AAPL")

        assert calls == ["first"]

    def test_different_key_not_suppressed(self, tmp_path: Path):
        """A different dedup_key is an independent suppression bucket."""
        from observability.alerts import send_alert
        calls: list[str] = []

        def fake_console(level, ts, message):
            calls.append(message)

        s = self._counting_settings(tmp_path, ALERT_DEDUP_WINDOW_SECONDS=900)
        with _patch("observability.alerts.settings", s):
            with _patch("observability.alerts._send_console", fake_console):
                send_alert("WARNING", "AAPL heat", channels=["console"], dedup_key="heat_AAPL")
                send_alert("WARNING", "MSFT heat", channels=["console"], dedup_key="heat_MSFT")

        assert calls == ["AAPL heat", "MSFT heat"]

    def test_same_key_after_window_elapses_fires_again(self, tmp_path: Path):
        """Once the TTL elapses, the same dedup_key fires a fresh alert."""
        from observability import alerts
        calls: list[str] = []

        def fake_console(level, ts, message):
            calls.append(message)

        fake_time = [1000.0]

        def fake_monotonic():
            return fake_time[0]

        s = self._counting_settings(tmp_path, ALERT_DEDUP_WINDOW_SECONDS=60)
        with _patch("observability.alerts.settings", s):
            with _patch("observability.alerts._send_console", fake_console):
                with _patch("observability.alerts.time.monotonic", fake_monotonic):
                    alerts.send_alert("CRITICAL", "first", channels=["console"], dedup_key="ks")
                    fake_time[0] += 30.0  # inside the 60s window — suppressed
                    alerts.send_alert("CRITICAL", "second", channels=["console"], dedup_key="ks")
                    fake_time[0] += 61.0  # now past the window from the first fire
                    alerts.send_alert("CRITICAL", "third", channels=["console"], dedup_key="ks")

        assert calls == ["first", "third"]

    def test_no_dedup_key_always_fires(self, tmp_path: Path):
        """Omitting dedup_key (the default) means every call dispatches — no suppression."""
        from observability.alerts import send_alert
        calls: list[str] = []

        def fake_console(level, ts, message):
            calls.append(message)

        s = self._counting_settings(tmp_path, ALERT_DEDUP_WINDOW_SECONDS=900)
        with _patch("observability.alerts.settings", s):
            with _patch("observability.alerts._send_console", fake_console):
                send_alert("INFO", "a", channels=["console"])
                send_alert("INFO", "b", channels=["console"])
                send_alert("INFO", "c", channels=["console"])

        assert calls == ["a", "b", "c"]

    def test_reset_dedup_state_clears_suppression(self, tmp_path: Path):
        """``reset_dedup_state()`` allows an immediately-following same-key alert to fire."""
        from observability import alerts
        calls: list[str] = []

        def fake_console(level, ts, message):
            calls.append(message)

        s = self._counting_settings(tmp_path, ALERT_DEDUP_WINDOW_SECONDS=900)
        with _patch("observability.alerts.settings", s):
            with _patch("observability.alerts._send_console", fake_console):
                alerts.send_alert("WARNING", "first", channels=["console"], dedup_key="x")
                alerts.reset_dedup_state()
                alerts.send_alert("WARNING", "second", channels=["console"], dedup_key="x")

        assert calls == ["first", "second"]


# ---------------------------------------------------------------------------
# Channel health-check / self-test (Phase O4)
# ---------------------------------------------------------------------------

class TestChannelHealth:
    """``check_channel_health()`` probes every active channel and reports
    per-channel reachability without ever raising, even when a channel is
    broken.
    """

    def test_console_only_reports_ok(self, tmp_path: Path):
        """With no webhook/email configured, only 'console' is probed and it reports ok."""
        from observability.alerts import check_channel_health
        s = _make_settings(tmp_path)  # all channels unconfigured
        with _patch("observability.alerts.settings", s):
            result = check_channel_health()
        assert result == {"console": {"ok": True, "error": None}}

    def test_healthy_discord_reports_ok(self, tmp_path: Path):
        """A reachable Discord webhook reports ok=True, error=None."""
        from observability.alerts import check_channel_health
        s = _make_settings(tmp_path, DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/x/y")

        def fake_urlopen(req, timeout=None):
            return _FakeOkResponse()

        with _patch("observability.alerts.settings", s):
            with _patch("observability.alerts.urllib.request.urlopen", fake_urlopen):
                result = check_channel_health()
        assert result["discord"] == {"ok": True, "error": None}
        assert result["console"]["ok"] is True

    def test_broken_discord_reports_failure_without_raising(self, tmp_path: Path):
        """A webhook that raises URLError is captured as ok=False with the error text."""
        from observability.alerts import check_channel_health
        s = _make_settings(tmp_path, DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/x/y")

        def fail(*a, **kw):
            raise urllib.error.URLError("connection refused")

        with _patch("observability.alerts.settings", s):
            with _patch("observability.alerts.urllib.request.urlopen", fail):
                result = check_channel_health()  # must not raise
        assert result["discord"]["ok"] is False
        assert "connection refused" in result["discord"]["error"]
        # console must still be probed and healthy despite discord's failure —
        # one channel's failure must never suppress the others.
        assert result["console"] == {"ok": True, "error": None}

    def test_broken_channel_never_touches_real_network_in_offline_test(self, tmp_path: Path):
        """Sanity: this test file makes zero real network calls (all urlopen mocked)."""
        from observability.alerts import check_channel_health
        s = _make_settings(
            tmp_path,
            SLACK_WEBHOOK_URL="https://hooks.slack.com/services/x/y",
        )
        calls: list[Any] = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            return _FakeOkResponse()

        with _patch("observability.alerts.settings", s):
            with _patch("observability.alerts.urllib.request.urlopen", fake_urlopen):
                result = check_channel_health()
        assert len(calls) == 1
        assert result["slack"]["ok"] is True

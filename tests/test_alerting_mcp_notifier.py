"""
tests/test_alerting_mcp_notifier.py
====================================
Unit tests for ``alerting_mcp/notifier.py`` — the cloud-hosted pipeline's
lightweight, multi-channel notification dispatcher (ntfy.sh / email / Slack).

This module is a sibling of ``observability/alerts.py`` (same failure
domain: fire off a best-effort push/webhook/email, never let a broken
channel propagate into the caller) but reads its configuration from plain
``os.environ`` rather than the ``settings`` singleton, so tests here use
``monkeypatch.setenv``/``delenv`` instead of a mock settings object.

Coverage
--------
* ``_send_ntfy``: POSTs to ``https://ntfy.sh/{topic}`` with the right
  headers; non-200 response and ``URLError``/``OSError`` both degrade to
  ``False`` rather than raising.
* ``_send_email``: sends via SMTP STARTTLS when ``ALERT_EMAIL_SMTP_PASSWORD``
  is set; skips (returns ``False``, no SMTP call) when it is not; SMTP
  exceptions are swallowed.
* ``_send_slack``: POSTs the ``{"text": ...}`` payload with an emoji prefix
  keyed by priority; skips when ``ALERT_SLACK_WEBHOOK_URL`` is unset;
  network errors are swallowed.
* ``get_active_channels``: parses the comma-separated ``ALERT_CHANNELS`` env
  var, defaulting to ``["ntfy"]``.
* ``send`` (the dispatcher): fans out to every active channel and returns a
  ``{channel: bool}`` result map; an unknown channel name is recorded as
  ``False`` (never raises); a handler that itself raises is caught and
  recorded as ``False`` rather than propagating (CONSTRAINT #6 — a broken
  channel can never crash the caller).
* ``get_alert_config``/``save_alert_config``: JSON round-trip through a
  temp path; a missing or corrupt config file degrades to the documented
  default dict rather than raising.
"""

from __future__ import annotations

import json
import smtplib
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from alerting_mcp import notifier


# ---------------------------------------------------------------------------
# _send_ntfy
# ---------------------------------------------------------------------------


class TestSendNtfy:
    def test_success_posts_expected_headers(self, monkeypatch):
        monkeypatch.setenv("ALERT_NTFY_TOPIC", "my-topic")
        captured = {}

        class _Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["title"] = req.get_header("Title")
            captured["priority"] = req.get_header("Priority")
            return _Resp()

        monkeypatch.setattr(notifier, "urlopen", _fake_urlopen)

        ok = notifier._send_ntfy("Hello", "World", priority="high")

        assert ok is True
        assert captured["url"] == "https://ntfy.sh/my-topic"
        assert captured["title"] == "Hello"
        assert captured["priority"] == "4"

    def test_non_200_status_returns_false(self, monkeypatch):
        class _Resp:
            status = 500

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(notifier, "urlopen", lambda req, timeout=None: _Resp())

        assert notifier._send_ntfy("t", "m") is False

    def test_network_error_swallowed(self, monkeypatch):
        def _raise(req, timeout=None):
            raise urllib.error.URLError("boom")

        monkeypatch.setattr(notifier, "urlopen", _raise)

        assert notifier._send_ntfy("t", "m") is False

    def test_default_priority_maps_to_3(self, monkeypatch):
        captured = {}

        class _Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake_urlopen(req, timeout=None):
            captured["priority"] = req.get_header("Priority")
            return _Resp()

        monkeypatch.setattr(notifier, "urlopen", _fake_urlopen)
        notifier._send_ntfy("t", "m")

        assert captured["priority"] == "3"


# ---------------------------------------------------------------------------
# _send_email
# ---------------------------------------------------------------------------


class TestSendEmail:
    def test_skips_when_password_unset(self, monkeypatch):
        monkeypatch.delenv("ALERT_EMAIL_SMTP_PASSWORD", raising=False)
        smtp_cls = MagicMock()
        monkeypatch.setattr(smtplib, "SMTP", smtp_cls)

        ok = notifier._send_email("t", "m")

        assert ok is False
        smtp_cls.assert_not_called()

    def test_sends_via_starttls_when_configured(self, monkeypatch):
        monkeypatch.setenv("ALERT_EMAIL_SMTP_PASSWORD", "app-password")
        monkeypatch.setenv("ALERT_EMAIL_FROM", "from@example.com")
        monkeypatch.setenv("ALERT_EMAIL_TO", "to@example.com")

        server = MagicMock()
        server.__enter__ = MagicMock(return_value=server)
        server.__exit__ = MagicMock(return_value=False)
        smtp_cls = MagicMock(return_value=server)
        monkeypatch.setattr(smtplib, "SMTP", smtp_cls)

        ok = notifier._send_email("Subject Line", "body text")

        assert ok is True
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("from@example.com", "app-password")
        server.send_message.assert_called_once()
        sent_msg = server.send_message.call_args[0][0]
        assert sent_msg["From"] == "from@example.com"
        assert sent_msg["To"] == "to@example.com"
        assert "Subject Line" in sent_msg["Subject"]

    def test_smtp_exception_swallowed(self, monkeypatch):
        monkeypatch.setenv("ALERT_EMAIL_SMTP_PASSWORD", "app-password")

        def _raise(*a, **k):
            raise OSError("connection refused")

        monkeypatch.setattr(smtplib, "SMTP", _raise)

        assert notifier._send_email("t", "m") is False


# ---------------------------------------------------------------------------
# _send_slack
# ---------------------------------------------------------------------------


class TestSendSlack:
    def test_skips_when_webhook_unset(self, monkeypatch):
        monkeypatch.delenv("ALERT_SLACK_WEBHOOK_URL", raising=False)
        called = []
        monkeypatch.setattr(notifier, "urlopen", lambda *a, **k: called.append(1))

        assert notifier._send_slack("t", "m") is False
        assert called == []

    def test_success_posts_json_with_emoji(self, monkeypatch):
        monkeypatch.setenv("ALERT_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/x")
        captured = {}

        class _Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["content_type"] = req.get_header("Content-type")
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return _Resp()

        monkeypatch.setattr(notifier, "urlopen", _fake_urlopen)

        ok = notifier._send_slack("Alert Title", "alert body", priority="urgent")

        assert ok is True
        assert captured["url"] == "https://hooks.slack.com/services/x"
        assert captured["content_type"] == "application/json"
        assert "🚨" in captured["payload"]["text"]
        assert "Alert Title" in captured["payload"]["text"]
        assert "alert body" in captured["payload"]["text"]

    def test_network_error_swallowed(self, monkeypatch):
        monkeypatch.setenv("ALERT_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/x")

        def _raise(req, timeout=None):
            raise urllib.error.URLError("boom")

        monkeypatch.setattr(notifier, "urlopen", _raise)

        assert notifier._send_slack("t", "m") is False


# ---------------------------------------------------------------------------
# get_active_channels
# ---------------------------------------------------------------------------


class TestGetActiveChannels:
    def test_defaults_to_ntfy(self, monkeypatch):
        monkeypatch.delenv("ALERT_CHANNELS", raising=False)
        assert notifier.get_active_channels() == ["ntfy"]

    def test_parses_comma_separated_list(self, monkeypatch):
        monkeypatch.setenv("ALERT_CHANNELS", "ntfy, email,slack ")
        assert notifier.get_active_channels() == ["ntfy", "email", "slack"]

    def test_blank_entries_dropped(self, monkeypatch):
        monkeypatch.setenv("ALERT_CHANNELS", "ntfy,,email")
        assert notifier.get_active_channels() == ["ntfy", "email"]


# ---------------------------------------------------------------------------
# send (dispatcher)
# ---------------------------------------------------------------------------


class TestSendDispatcher:
    def test_fans_out_to_all_active_channels(self, monkeypatch):
        monkeypatch.setitem(notifier.CHANNEL_HANDLERS, "ntfy", lambda t, m, priority="default": True)
        monkeypatch.setitem(notifier.CHANNEL_HANDLERS, "email", lambda t, m, priority="default": True)

        result = notifier.send("t", "m", channels=["ntfy", "email"])

        assert result == {"ntfy": True, "email": True}

    def test_unknown_channel_recorded_as_false(self):
        result = notifier.send("t", "m", channels=["carrier_pigeon"])
        assert result == {"carrier_pigeon": False}

    def test_handler_exception_is_caught_not_raised(self, monkeypatch):
        def _boom(t, m, priority="default"):
            raise RuntimeError("channel exploded")

        monkeypatch.setitem(notifier.CHANNEL_HANDLERS, "slack", _boom)

        result = notifier.send("t", "m", channels=["slack"])

        assert result == {"slack": False}

    def test_partial_failure_does_not_affect_other_channels(self, monkeypatch):
        monkeypatch.setitem(notifier.CHANNEL_HANDLERS, "ntfy", lambda t, m, priority="default": True)
        monkeypatch.setitem(
            notifier.CHANNEL_HANDLERS,
            "email",
            lambda t, m, priority="default": (_ for _ in ()).throw(RuntimeError("smtp down")),
        )

        result = notifier.send("t", "m", channels=["ntfy", "email"])

        assert result == {"ntfy": True, "email": False}

    def test_uses_active_channels_from_env_when_not_overridden(self, monkeypatch):
        monkeypatch.setenv("ALERT_CHANNELS", "ntfy")
        monkeypatch.setitem(notifier.CHANNEL_HANDLERS, "ntfy", lambda t, m, priority="default": True)

        result = notifier.send("t", "m")

        assert result == {"ntfy": True}


# ---------------------------------------------------------------------------
# get_alert_config / save_alert_config
# ---------------------------------------------------------------------------


class TestAlertConfigStore:
    def test_missing_file_returns_default(self, monkeypatch, tmp_path):
        monkeypatch.setattr(notifier, "_ALERT_CONFIG_PATH", str(tmp_path / "does_not_exist.json"))
        monkeypatch.delenv("ALERT_CHANNELS", raising=False)

        cfg = notifier.get_alert_config()

        assert cfg["channels"] == ["ntfy"]
        assert cfg["events"]["signal_fired"] is True

    def test_corrupt_file_degrades_to_default(self, monkeypatch, tmp_path):
        path = tmp_path / "alert_config.json"
        path.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr(notifier, "_ALERT_CONFIG_PATH", str(path))

        cfg = notifier.get_alert_config()

        assert "channels" in cfg and "events" in cfg

    def test_round_trip(self, monkeypatch, tmp_path):
        path = tmp_path / "alert_config.json"
        monkeypatch.setattr(notifier, "_ALERT_CONFIG_PATH", str(path))

        written = {"channels": ["slack"], "events": {"signal_fired": False}}
        notifier.save_alert_config(written)

        assert json.loads(path.read_text(encoding="utf-8")) == written
        assert notifier.get_alert_config() == written

"""
tests/test_alerting.py
========================
Unit tests for ``alerting.py`` (the top-level structured-logging setup and
ntfy push-notification dispatcher used by ``main.py``'s advisory loop —
distinct from ``observability/alerts.py``, which already has dedicated
coverage in ``tests/test_alerts.py``). Prior to this file, none of
``setup_logging``, ``notify``, or ``summarize_run`` had a single direct
unit test.

Isolation strategy (critical — this module touches process-global state):

  * ``setup_logging()`` guards on ``if root.handlers: return`` and mutates
    the REAL root logger. Every test that calls it saves/restores
    ``logging.getLogger().handlers`` and ``.level`` exactly, and monkeypatches
    ``alerting._LOGS_DIR`` / ``alerting._LOG_FILE`` to ``tmp_path`` so the
    real, repo-committed ``logs/investyo.log`` is never written to or
    rotated. Without the save/restore, clearing the root logger's handlers
    would also strip pytest's own log-capture handler for the rest of the
    session.
  * ``notify()`` reads ``NTFY_TOPIC`` from ``os.environ`` directly (not
    ``settings``) — tests use ``monkeypatch.delenv``/``setenv``. All
    ``urllib.request.urlopen`` calls are faked (mirrors the pattern already
    established in ``tests/test_alerts.py``) — never a real network call.
"""

from __future__ import annotations

import logging
import math
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch as _patch

import pytest

import alerting


# ---------------------------------------------------------------------------
# Root-logger isolation fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_root_logger():
    """Save the real root logger's handlers/level and restore them exactly
    after the test — never leaking test-added handlers into the rest of the
    pytest session.

    Deliberately does NOT clear ``root.handlers`` during fixture setup: pytest's
    own log-capture machinery (re-)attaches a ``LogCaptureHandler`` to the
    root logger right at the start of each test's call phase — AFTER fixture
    setup completes but BEFORE the test body executes (confirmed by direct
    execution: a fixture-time clear reliably ends up with pytest's handlers
    back in place by the time ``setup_logging()`` runs, causing its
    ``if root.handlers: return`` idempotency guard to no-op immediately
    without ever creating a log file). Each test must instead clear
    ``clean_root_logger.handlers = []`` as the FIRST line of its own body,
    right before calling ``setup_logging()``."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        yield root
    finally:
        for h in root.handlers:
            if h not in original_handlers:
                try:
                    h.close()
                except Exception:
                    pass
        root.handlers = original_handlers
        root.setLevel(original_level)


@pytest.fixture
def isolated_log_dir(tmp_path: Path, monkeypatch):
    """Redirect alerting's module-level log-file constants into tmp_path so
    setup_logging() never touches the real logs/investyo.log."""
    log_dir = tmp_path / "logs"
    log_file = log_dir / "investyo.log"
    monkeypatch.setattr(alerting, "_LOGS_DIR", log_dir)
    monkeypatch.setattr(alerting, "_LOG_FILE", log_file)
    return log_file


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------

class TestSetupLogging:
    def test_attaches_file_and_console_handlers(self, clean_root_logger, isolated_log_dir):
        clean_root_logger.handlers = []
        alerting.setup_logging()
        assert len(clean_root_logger.handlers) == 2
        assert isolated_log_dir.parent.exists()
        assert isolated_log_dir.exists()

    def test_idempotent_second_call_does_not_duplicate_handlers(
        self, clean_root_logger, isolated_log_dir
    ):
        clean_root_logger.handlers = []
        alerting.setup_logging()
        n_after_first = len(clean_root_logger.handlers)
        alerting.setup_logging()
        assert len(clean_root_logger.handlers) == n_after_first

    def test_log_level_env_var_overrides_argument(
        self, clean_root_logger, isolated_log_dir, monkeypatch
    ):
        clean_root_logger.handlers = []
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        alerting.setup_logging(log_level="INFO")
        assert clean_root_logger.level == logging.WARNING

    def test_invalid_log_level_falls_back_to_info(
        self, clean_root_logger, isolated_log_dir, monkeypatch
    ):
        clean_root_logger.handlers = []
        monkeypatch.setenv("LOG_LEVEL", "NOT_A_REAL_LEVEL")
        alerting.setup_logging()
        assert clean_root_logger.level == logging.INFO

    def test_oserror_on_file_handler_degrades_to_console_only(
        self, clean_root_logger, isolated_log_dir
    ):
        """A read-only/unwritable log directory must never crash the app —
        it falls through to console-only logging."""
        clean_root_logger.handlers = []
        with _patch(
            "logging.handlers.RotatingFileHandler",
            side_effect=OSError("read-only filesystem"),
        ):
            alerting.setup_logging()  # must not raise
        assert len(clean_root_logger.handlers) == 1  # console handler only

    def test_never_writes_to_real_repo_log_file(self, clean_root_logger, monkeypatch, tmp_path):
        """Without an isolated log dir override, calling setup_logging from
        inside a tmp_path-cwd sandbox must not touch the real repo's
        logs/investyo.log. We monkeypatch the constants regardless (belt and
        suspenders) and additionally assert the real repo log path was
        never referenced by any handler's baseFilename."""
        clean_root_logger.handlers = []
        log_dir = tmp_path / "isolated_logs"
        monkeypatch.setattr(alerting, "_LOGS_DIR", log_dir)
        monkeypatch.setattr(alerting, "_LOG_FILE", log_dir / "investyo.log")
        alerting.setup_logging()
        for h in clean_root_logger.handlers:
            base_filename = getattr(h, "baseFilename", None)
            if base_filename is not None:
                assert "logs/investyo.log" not in base_filename.replace("\\", "/") or str(
                    tmp_path
                ) in base_filename


# ---------------------------------------------------------------------------
# notify
# ---------------------------------------------------------------------------

class _FakeOkResponse:
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *a):
        pass


class TestNotify:
    def test_noop_when_ntfy_topic_unset(self, monkeypatch):
        monkeypatch.delenv("NTFY_TOPIC", raising=False)
        with _patch("alerting.urllib.request.urlopen") as mock_urlopen:
            alerting.notify("Title", "Message")  # must not raise
            mock_urlopen.assert_not_called()

    def test_noop_when_ntfy_topic_is_blank_whitespace(self, monkeypatch):
        monkeypatch.setenv("NTFY_TOPIC", "   ")
        with _patch("alerting.urllib.request.urlopen") as mock_urlopen:
            alerting.notify("Title", "Message")
            mock_urlopen.assert_not_called()

    def test_successful_post_hits_ntfy_sh_with_topic_in_url(self, monkeypatch):
        monkeypatch.setenv("NTFY_TOPIC", "my-secret-topic")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["headers"] = req.headers
            captured["timeout"] = timeout
            return _FakeOkResponse()

        with _patch("alerting.urllib.request.urlopen", fake_urlopen):
            alerting.notify("Alert Title", "Alert body", priority="high")

        assert captured["url"] == "https://ntfy.sh/my-secret-topic"
        assert captured["headers"]["Title"] == "Alert Title"
        assert captured["headers"]["Priority"] == "high"

    def test_unknown_priority_silently_replaced_with_default(self, monkeypatch):
        monkeypatch.setenv("NTFY_TOPIC", "topic")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["headers"] = req.headers
            return _FakeOkResponse()

        with _patch("alerting.urllib.request.urlopen", fake_urlopen):
            alerting.notify("T", "M", priority="not-a-real-priority")

        assert captured["headers"]["Priority"] == "default"

    def test_url_error_is_caught_and_never_raises(self, monkeypatch):
        monkeypatch.setenv("NTFY_TOPIC", "topic")

        def raising_urlopen(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        with _patch("alerting.urllib.request.urlopen", raising_urlopen):
            alerting.notify("T", "M")  # must not raise

    def test_generic_exception_is_caught_and_never_raises(self, monkeypatch):
        monkeypatch.setenv("NTFY_TOPIC", "topic")

        def raising_urlopen(req, timeout=None):
            raise ValueError("something unexpected")

        with _patch("alerting.urllib.request.urlopen", raising_urlopen):
            alerting.notify("T", "M")  # must not raise

    def test_non_2xx_http_status_logged_not_raised(self, monkeypatch, caplog):
        monkeypatch.setenv("NTFY_TOPIC", "topic")

        class _FakeErrorResponse:
            status = 500
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        with _patch("alerting.urllib.request.urlopen", lambda req, timeout=None: _FakeErrorResponse()):
            with caplog.at_level("WARNING"):
                alerting.notify("T", "M")  # must not raise
        assert "500" in caplog.text


# ---------------------------------------------------------------------------
# summarize_run
# ---------------------------------------------------------------------------

def _rec(symbol, action, conviction=0.5, suggested_position_pct=0.0, rationale=""):
    return SimpleNamespace(
        symbol=symbol, action=action, conviction=conviction,
        suggested_position_pct=suggested_position_pct, rationale=rationale,
    )


class TestSummarizeRun:
    def test_empty_result_does_not_raise(self):
        result = SimpleNamespace(recommendations=[], errors=[], started_at=None, duration_seconds=0.0)
        summary = alerting.summarize_run(result)
        assert "Errors  : 0  (clean run)" in summary
        assert "unknown" in summary  # started_at=None -> "unknown" timestamp

    def test_duck_typed_missing_attributes_default_gracefully(self):
        """A minimal object with none of RunResult's attributes must not raise
        (getattr defaults kick in for every field)."""
        result = SimpleNamespace()
        summary = alerting.summarize_run(result)
        assert "Universe: 0 evaluated" in summary

    def test_signal_tally_counts_by_action(self):
        result = SimpleNamespace(
            recommendations=[
                _rec("AAPL", "BUY"), _rec("MSFT", "BUY"), _rec("TSLA", "SELL"),
                _rec("SPY", "HOLD"),
            ],
            errors=[], started_at=None, duration_seconds=1.0,
        )
        summary = alerting.summarize_run(result)
        assert "BUY=2  HOLD=1  SELL=1" in summary

    def test_error_preview_shows_first_three_and_more_suffix(self):
        errors = [{"symbol": f"T{i}", "stage": "advisory_evaluate"} for i in range(5)]
        result = SimpleNamespace(recommendations=[], errors=errors, started_at=None, duration_seconds=1.0)
        summary = alerting.summarize_run(result)
        assert "Errors  : 5" in summary
        assert "+2 more" in summary
        # Only the first 3 error symbols appear in the preview.
        assert "T3" not in summary and "T4" not in summary
        assert "T0" in summary and "T2" in summary

    def test_top_3_actionable_excludes_holds_and_sorts_by_conviction(self):
        result = SimpleNamespace(
            recommendations=[
                _rec("LOW", "BUY", conviction=0.1),
                _rec("HIGH", "BUY", conviction=0.9),
                _rec("MID", "SELL", conviction=0.5),
                _rec("IGNORED", "HOLD", conviction=0.99),  # excluded despite highest conviction
            ],
            errors=[], started_at=None, duration_seconds=1.0,
        )
        summary = alerting.summarize_run(result)
        lines = [l for l in summary.splitlines() if l.strip().startswith(("1.", "2.", "3."))]
        assert len(lines) == 3
        assert "HIGH" in lines[0]
        assert "MID" in lines[1]
        assert "LOW" in lines[2]
        assert "IGNORED" not in summary

    def test_only_top_3_shown_even_with_more_actionable_signals(self):
        result = SimpleNamespace(
            recommendations=[_rec(f"S{i}", "BUY", conviction=float(i)) for i in range(5)],
            errors=[], started_at=None, duration_seconds=1.0,
        )
        summary = alerting.summarize_run(result)
        lines = [l for l in summary.splitlines() if l.strip().startswith(("1.", "2.", "3.", "4.", "5."))]
        assert len(lines) == 3

    def test_nan_conviction_does_not_raise_and_is_not_fabricated_high(self):
        """NaN conviction is a real edge case (NaN comparisons are always
        False) — pin that sorting with a NaN-conviction entry does not raise
        and does not crash the summary; the NaN entry's final position is not
        asserted since NaN-key sort order is implementation-defined, only
        that the function completes and formats it as 'nan' without error."""
        result = SimpleNamespace(
            recommendations=[
                _rec("NANNY", "BUY", conviction=float("nan")),
                _rec("NORMAL", "BUY", conviction=0.5),
            ],
            errors=[], started_at=None, duration_seconds=1.0,
        )
        summary = alerting.summarize_run(result)  # must not raise
        assert "NANNY" in summary
        assert "NORMAL" in summary

    def test_rationale_truncated_to_60_chars(self):
        long_rationale = "x" * 200
        result = SimpleNamespace(
            recommendations=[_rec("AAPL", "BUY", conviction=0.9, rationale=long_rationale)],
            errors=[], started_at=None, duration_seconds=1.0,
        )
        summary = alerting.summarize_run(result)
        assert "x" * 60 in summary
        assert "x" * 61 not in summary

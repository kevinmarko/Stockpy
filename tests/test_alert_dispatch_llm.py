"""
tests/test_alert_dispatch_llm.py
=================================
Unit tests for the Tier 9 LLM augment hook in ``watch_engine.dispatch_watch_alerts``.
(The second dispatch site, ``engine.trade_signals.dispatch_trade_alerts``, was
archived with ``main.py`` in shrink step 5.5; its tests are in ``legacy/tests/``.)

The dispatch site must:

* Leave the template ``msg`` unchanged when ``LLM_COMMENTARY_ENABLED=False``.
* APPEND the LLM body (with the 📝 marker) when LLM is enabled AND the
  commentary call succeeds.
* Leave the template ``msg`` unchanged on soft-fail (LLM returns None) —
  never swallow the trigger or replace the message.
* NEVER override ``alert.priority`` from the LLM's ``urgency_hint``.

The deterministic ``alerting.notify`` call is monkeypatched so no network
traffic is generated.
"""

from __future__ import annotations

from typing import List, Tuple
from unittest import mock

import pytest

from llm.schemas import AlertCommentary
from watch_engine import WatchAlert, dispatch_watch_alerts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_watch_alert() -> WatchAlert:
    return WatchAlert(
        symbol="AAPL",
        rule_type="conviction_above",
        priority="high",
        title="High Conviction Alert",
        message="AAPL conviction crossed 0.85 (current: 0.87).",
        trigger_detail="0.85 → 0.87",
    )


def _capture_notify(monkeypatch, target_module: str) -> List[Tuple[str, str, str]]:
    """Patch ``alerting.notify`` in-place so dispatch sees a no-op callable.

    The dispatch site does ``from alerting import notify`` inline, so the
    monkeypatch must target the ``alerting`` module itself.
    """
    captured: List[Tuple[str, str, str]] = []

    def _stub(title, message, priority="default"):
        captured.append((title, message, priority))

    import alerting

    monkeypatch.setattr(alerting, "notify", _stub)
    return captured


# ---------------------------------------------------------------------------
# TestWatchEngineLLMDisabled
# ---------------------------------------------------------------------------


class TestWatchEngineLLMDisabled:
    def test_master_switch_off_leaves_template_unchanged(self, monkeypatch):
        from watch_engine import settings as we_settings

        monkeypatch.setattr(we_settings, "LLM_COMMENTARY_ENABLED", False, raising=False)
        captured = _capture_notify(monkeypatch, "watch_engine")

        wa = _make_watch_alert()
        dispatch_watch_alerts([wa])
        assert len(captured) == 1
        title, message, priority = captured[0]
        assert message == wa.message
        assert "📝" not in message
        assert priority == "high"


# ---------------------------------------------------------------------------
# TestWatchEngineLLMSuccess
# ---------------------------------------------------------------------------


class TestWatchEngineLLMSuccess:
    def test_llm_body_is_appended_with_marker(self, monkeypatch, tmp_path):
        from llm import cache as cache_mod
        from llm import commentary as commentary_mod
        from watch_engine import settings as we_settings

        # Isolated cache so this test never sees stale entries.
        monkeypatch.setattr(
            cache_mod.settings,
            "LLM_COMMENTARY_CACHE_PATH",
            str(tmp_path / "cache.json"),
            raising=False,
        )
        monkeypatch.setattr(we_settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)

        class _Prov:
            name = "gemini"

            def call_structured(self, *a, **kw):
                return AlertCommentary(body="Cleared multi-cycle resistance.", urgency_hint="high")

        monkeypatch.setattr(commentary_mod, "get_alert_provider", lambda: _Prov())
        captured = _capture_notify(monkeypatch, "watch_engine")

        wa = _make_watch_alert()
        dispatch_watch_alerts([wa])
        assert len(captured) == 1
        title, message, priority = captured[0]
        # Template is preserved AND appended-to (never replaced).
        assert message.startswith(wa.message)
        assert "📝" in message
        assert "Cleared multi-cycle resistance." in message
        # Priority comes from the rule, never the LLM.
        assert priority == "high"


# ---------------------------------------------------------------------------
# TestWatchEngineLLMSoftFail
# ---------------------------------------------------------------------------


class TestWatchEngineLLMSoftFail:
    def test_provider_returns_none_leaves_template_unchanged(self, monkeypatch, tmp_path):
        from llm import cache as cache_mod
        from llm import commentary as commentary_mod
        from watch_engine import settings as we_settings

        monkeypatch.setattr(
            cache_mod.settings,
            "LLM_COMMENTARY_CACHE_PATH",
            str(tmp_path / "cache.json"),
            raising=False,
        )
        monkeypatch.setattr(we_settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(commentary_mod, "get_alert_provider", lambda: None)
        captured = _capture_notify(monkeypatch, "watch_engine")

        wa = _make_watch_alert()
        dispatch_watch_alerts([wa])
        assert len(captured) == 1
        _t, message, _p = captured[0]
        assert message == wa.message
        assert "📝" not in message

    def test_commentary_raises_does_not_block_dispatch(self, monkeypatch, tmp_path):
        from llm import cache as cache_mod
        from llm import commentary as commentary_mod
        from watch_engine import settings as we_settings

        monkeypatch.setattr(
            cache_mod.settings,
            "LLM_COMMENTARY_CACHE_PATH",
            str(tmp_path / "cache.json"),
            raising=False,
        )
        monkeypatch.setattr(we_settings, "LLM_COMMENTARY_ENABLED", True, raising=False)

        def _raise(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(commentary_mod, "generate_alert_commentary", _raise)
        captured = _capture_notify(monkeypatch, "watch_engine")

        wa = _make_watch_alert()
        dispatch_watch_alerts([wa])
        # Dispatch must still have happened with the template message.
        assert len(captured) == 1
        _t, message, _p = captured[0]
        assert message == wa.message

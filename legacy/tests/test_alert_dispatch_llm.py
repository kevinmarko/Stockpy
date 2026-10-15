"""
legacy/tests/test_alert_dispatch_llm.py
=======================================
The ``engine.trade_signals.dispatch_trade_alerts`` half of
``tests/test_alert_dispatch_llm.py`` (the LLM augment hook on the advisory
trade-signal pushes), split out when ``engine/trade_signals.py`` was archived
with ``main.py`` in shrink step 5.5. The ``watch_engine`` half stays active.

Keeps its original imports: restore ``engine/trade_signals.py`` to run it.
"""

from __future__ import annotations

from typing import List, Tuple

from engine.trade_signals import TradeAlert, dispatch_trade_alerts
from llm.schemas import AlertCommentary


def _make_trade_alert() -> TradeAlert:
    return TradeAlert(
        symbol="AAPL",
        kind="momentum_building",
        priority="default",
        title="AAPL — momentum building",
        message="Conviction trajectory rose 0.61 → 0.74 over the last 4 cycles.",
        detail={"conv_now": 0.74, "conv_prev": 0.61},
    )


def _capture_notify(monkeypatch, target_module: str) -> List[Tuple[str, str, str]]:
    """Patch ``alerting.notify`` in-place so dispatch sees a no-op callable.

    Both dispatch sites do ``from alerting import notify`` inline, so the
    monkeypatch must target the ``alerting`` module itself.
    """
    captured: List[Tuple[str, str, str]] = []

    def _stub(title, message, priority="default"):
        captured.append((title, message, priority))

    import alerting

    monkeypatch.setattr(alerting, "notify", _stub)
    return captured


# ---------------------------------------------------------------------------
# TestTradeSignalsLLMMirror
# ---------------------------------------------------------------------------


class TestTradeSignalsLLMDisabled:
    def test_master_switch_off_leaves_template_unchanged(self, monkeypatch):
        from engine.trade_signals import settings as ts_settings

        monkeypatch.setattr(ts_settings, "LLM_COMMENTARY_ENABLED", False, raising=False)
        captured = _capture_notify(monkeypatch, "engine.trade_signals")

        ta = _make_trade_alert()
        dispatch_trade_alerts([ta])
        assert len(captured) == 1
        _t, message, priority = captured[0]
        assert message == ta.message
        assert "📝" not in message
        assert priority == "default"


class TestTradeSignalsLLMSuccess:
    def test_llm_body_is_appended_with_marker(self, monkeypatch, tmp_path):
        from engine.trade_signals import settings as ts_settings
        from llm import cache as cache_mod
        from llm import commentary as commentary_mod

        monkeypatch.setattr(
            cache_mod.settings,
            "LLM_COMMENTARY_CACHE_PATH",
            str(tmp_path / "cache.json"),
            raising=False,
        )
        monkeypatch.setattr(ts_settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)

        class _Prov:
            name = "gemini"

            def call_structured(self, *a, **kw):
                return AlertCommentary(body="Trajectory accelerating into resistance.", urgency_hint="normal")

        monkeypatch.setattr(commentary_mod, "get_alert_provider", lambda: _Prov())
        captured = _capture_notify(monkeypatch, "engine.trade_signals")

        ta = _make_trade_alert()
        dispatch_trade_alerts([ta])
        assert len(captured) == 1
        _t, message, priority = captured[0]
        assert message.startswith(ta.message)
        assert "📝" in message
        assert "Trajectory accelerating into resistance." in message
        # Deterministic priority kept.
        assert priority == "default"


class TestTradeSignalsLLMSoftFail:
    def test_provider_returns_none_leaves_template_unchanged(self, monkeypatch, tmp_path):
        from engine.trade_signals import settings as ts_settings
        from llm import cache as cache_mod
        from llm import commentary as commentary_mod

        monkeypatch.setattr(
            cache_mod.settings,
            "LLM_COMMENTARY_CACHE_PATH",
            str(tmp_path / "cache.json"),
            raising=False,
        )
        monkeypatch.setattr(ts_settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(commentary_mod, "get_alert_provider", lambda: None)
        captured = _capture_notify(monkeypatch, "engine.trade_signals")

        ta = _make_trade_alert()
        dispatch_trade_alerts([ta])
        assert len(captured) == 1
        _t, message, _p = captured[0]
        assert message == ta.message
        assert "📝" not in message

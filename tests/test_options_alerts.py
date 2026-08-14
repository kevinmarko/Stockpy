"""
tests/test_options_alerts.py
=============================
Unit tests for pilots/options_alerts.py (Options Real-Time Alert Dispatcher).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch
import urllib.error

import pytest

from pilots.options_alerts import (
    DEFAULT_EARNINGS_CRUSH_MIN_EDGE,
    DEFAULT_UOA_WHALE_MIN_NOTIONAL,
    DEFAULT_UOA_WHALE_MIN_VOL_OI,
    dispatch_delta_hedge_alert,
    dispatch_earnings_crush_alert,
    dispatch_options_alert,
    dispatch_uoa_whale_alert,
    format_options_alert_message,
    post_webhook,
)
from pilots.unusual_options_flow import UOARecord
from settings import settings


class _FakeResponse:
    """Context manager for mocking urllib.request.urlopen responses."""

    def __init__(self, status: int = 200, error: bool = False):
        self.status = status
        self._error = error

    def __enter__(self):
        if self._error:
            raise urllib.error.URLError("Connection refused")
        return self

    def __exit__(self, *args):
        pass

    def getcode(self):
        return self.status


# ---------------------------------------------------------------------------
# AST & Architecture Safety
# ---------------------------------------------------------------------------

def test_options_alerts_ast_import_safety():
    """Verifies that pilots/options_alerts.py never imports heavy orchestrators or engines."""
    file_path = Path(__file__).resolve().parent.parent / "pilots" / "options_alerts.py"
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    forbidden = {
        "processing_engine",
        "strategy_engine",
        "forecasting_engine",
        "macro_engine",
        "technical_options_engine",
        "main_orchestrator",
        "desktop",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for target in forbidden:
                    assert target not in alias.name, f"Forbidden import found: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for target in forbidden:
                    assert target not in node.module, f"Forbidden from-import found: {node.module}"


def test_settings_options_alert_registration():
    """Verifies OPTIONS_ALERT_WEBHOOK_URL is registered on settings."""
    assert hasattr(settings, "OPTIONS_ALERT_WEBHOOK_URL")


# ---------------------------------------------------------------------------
# Direct Webhook Dispatching (post_webhook)
# ---------------------------------------------------------------------------

def test_post_webhook_empty_url():
    """Empty or None webhook URL returns clean error dictionary without raising."""
    res_none = post_webhook(None, "Test message")
    assert res_none["ok"] is False
    assert "No webhook URL" in res_none["error"]

    res_empty = post_webhook("   ", "Test message")
    assert res_empty["ok"] is False


def test_post_webhook_discord():
    """Discord webhooks format message into {'content': '...'} JSON body."""
    captured: list[Any] = []

    def fake_urlopen(req, timeout=None):
        captured.append(req)
        return _FakeResponse(status=204)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        url = "https://discord.com/api/webhooks/12345/abcdef"
        res = post_webhook(url, "Whale detected in AAPL", level="WARNING")

        assert res["ok"] is True
        assert res["status"] == 204
        assert len(captured) == 1

        req = captured[0]
        assert req.full_url == url
        assert req.headers["Content-type"] == "application/json"
        body = json.loads(req.data.decode("utf-8"))
        assert "content" in body
        assert "Whale detected in AAPL" in body["content"]
        assert "[WARNING]" in body["content"]


def test_post_webhook_slack():
    """Slack webhooks format message into {'text': '...'} JSON body."""
    captured: list[Any] = []

    def fake_urlopen(req, timeout=None):
        captured.append(req)
        return _FakeResponse(status=200)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        url = "https://hooks.slack.com/services/T00/B00/XXXX"
        res = post_webhook(url, "Delta imbalance on SPY", level="CRITICAL")

        assert res["ok"] is True
        assert res["status"] == 200
        assert len(captured) == 1

        req = captured[0]
        body = json.loads(req.data.decode("utf-8"))
        assert "text" in body
        assert "Delta imbalance on SPY" in body["text"]
        assert "*[CRITICAL]*" in body["text"]


def test_post_webhook_generic_endpoint():
    """Generic webhooks format structured payload containing level, text, timestamp, and extra."""
    captured: list[Any] = []

    def fake_urlopen(req, timeout=None):
        captured.append(req)
        return _FakeResponse(status=200)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        url = "https://api.example.com/alerts/webhook"
        extra = {"symbol": "NVDA", "edge": 1.45}
        res = post_webhook(url, "Earnings Crush Setup", level="INFO", extra=extra)

        assert res["ok"] is True
        req = captured[0]
        body = json.loads(req.data.decode("utf-8"))
        assert body["level"] == "INFO"
        assert body["extra"]["symbol"] == "NVDA"
        assert body["extra"]["edge"] == 1.45


def test_post_webhook_network_error_graceful():
    """Network errors in post_webhook are caught and returned as {'ok': False} without raising."""
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        res = post_webhook("https://discord.com/api/webhooks/123/abc", "Fail test")
        assert res["ok"] is False
        assert "Connection refused" in res["error"]


# ---------------------------------------------------------------------------
# UOA Whale Sweeps Alerting (dispatch_uoa_whale_alert)
# ---------------------------------------------------------------------------

def test_dispatch_uoa_whale_alert_none_input():
    """Passing None record returns dispatched=False."""
    res = dispatch_uoa_whale_alert(None)
    assert res["dispatched"] is False
    assert "No UOA record" in res["reason"]


def test_dispatch_uoa_whale_alert_below_threshold():
    """Records below Vol/OI >= 5.0 or Notional >= $250k do not dispatch."""
    # Low Vol/OI ratio (2.0 < 5.0)
    rec1 = {
        "symbol": "TSLA",
        "volume": 2000,
        "open_interest": 1000,
        "vol_oi_ratio": 2.0,
        "notional": 300000.0,
    }
    res1 = dispatch_uoa_whale_alert(rec1)
    assert res1["dispatched"] is False
    assert "whale criteria" in res1["reason"]

    # Low Notional ($100k < $250k)
    rec2 = {
        "symbol": "TSLA",
        "volume": 6000,
        "open_interest": 1000,
        "vol_oi_ratio": 6.0,
        "notional": 100000.0,
    }
    res2 = dispatch_uoa_whale_alert(rec2)
    assert res2["dispatched"] is False


def test_dispatch_uoa_whale_alert_success_dict():
    """Valid dict with Vol/OI >= 5.0 and Notional >= $250k dispatches successfully."""
    rec = {
        "symbol": "NVDA",
        "contract_symbol": "NVDA260918C00150000",
        "expiration": "2026-09-18",
        "strike": 150.0,
        "option_type": "call",
        "volume": 8500,
        "open_interest": 1200,
        "vol_oi_ratio": 7.08,
        "notional": 425000.0,
        "trade_price": 5.00,
        "aggressiveness": "ask_sweep",
        "sentiment": "BULLISH",
        "iv": 0.55,
        "hv_30": 0.40,
        "iv_burst_score": 1.38,
        "dte": 35,
    }

    with patch("observability.alerts.send_alert") as mock_send:
        res = dispatch_uoa_whale_alert(rec)

        assert res["dispatched"] is True
        assert res["level"] in ("WARNING", "CRITICAL")
        assert "NVDA" in res["message"]
        assert "$150.00 CALL" in res["message"]
        assert "$425,000.00" in res["message"]
        assert "7.1x" in res["message"] or "7.08" in str(res["extra"])
        assert "observability" in res["channels"]
        assert mock_send.call_count == 1


def test_dispatch_uoa_whale_alert_dataclass():
    """UOARecord dataclass instance dispatches accurately."""
    record = UOARecord(
        symbol="AAPL",
        contract_symbol="AAPL 2026-09-18 $220.00 PUT",
        expiration="2026-09-18",
        strike=220.0,
        option_type="put",
        trade_price=6.50,
        volume=10000,
        open_interest=1500,
        vol_oi_ratio=6.67,
        notional=650000.0,
        aggressiveness="ask_sweep",
        sentiment="BEARISH",
        iv=0.48,
        hv_30=0.32,
        iv_burst_score=1.50,
        dte=35,
    )

    with patch("observability.alerts.send_alert") as mock_send:
        res = dispatch_uoa_whale_alert(record)
        assert res["dispatched"] is True
        assert "AAPL" in res["message"]
        assert "BEARISH" in res["message"]
        assert "$650,000.00" in res["message"]
        assert mock_send.call_count == 1


def test_dispatch_uoa_whale_alert_with_custom_webhook():
    """Direct webhook_url dispatches both to observability and custom webhook."""
    rec = {
        "symbol": "AMD",
        "strike": 160.0,
        "option_type": "call",
        "volume": 5000,
        "open_interest": 500,
        "vol_oi_ratio": 10.0,
        "notional": 300000.0,
        "trade_price": 6.0,
    }

    with patch("observability.alerts.send_alert"), patch(
        "urllib.request.urlopen", return_value=_FakeResponse(status=200)
    ):
        res = dispatch_uoa_whale_alert(rec, webhook_url="https://hooks.slack.com/services/T/B/X")
        assert res["dispatched"] is True
        assert "custom_webhook" in res["channels"]
        assert res["webhook_status"] == 200


def test_dispatch_uoa_whale_alert_force():
    """force=True dispatches even if metrics are below whale threshold."""
    small_rec = {
        "symbol": "SPY",
        "vol_oi_ratio": 1.2,
        "notional": 50000.0,
        "volume": 200,
        "open_interest": 1000,
    }
    with patch("observability.alerts.send_alert"):
        res = dispatch_uoa_whale_alert(small_rec, force=True)
        assert res["dispatched"] is True


# ---------------------------------------------------------------------------
# Earnings Volatility Crush Alerting (dispatch_earnings_crush_alert)
# ---------------------------------------------------------------------------

def test_dispatch_earnings_crush_alert_none_input():
    """Passing None candidate returns dispatched=False."""
    res = dispatch_earnings_crush_alert(None)
    assert res["dispatched"] is False
    assert "No earnings crush candidate" in res["reason"]


def test_dispatch_earnings_crush_alert_below_edge():
    """Candidates with edge < 1.35x are rejected unless forced."""
    candidate = {
        "symbol": "GOOGL",
        "crush_edge_ratio": 1.20,
        "expected_move_pct": 0.06,
        "realized_move_pct": 0.05,
    }
    res = dispatch_earnings_crush_alert(candidate)
    assert res["dispatched"] is False
    assert "below minimum threshold" in res["reason"]


def test_dispatch_earnings_crush_alert_success():
    """Candidates with edge >= 1.35x format Iron Condor details and dispatch."""
    candidate = {
        "symbol": "META",
        "spot": 520.0,
        "earnings_date": "2026-08-20",
        "days_to_earnings": 2,
        "expiration": "2026-08-22",
        "dte": 4,
        "atm_iv": 0.65,
        "expected_move_usd": 38.0,
        "expected_move_pct": 0.073,
        "realized_move_pct": 0.048,
        "crush_edge_ratio": 1.52,
        "strategy": "Iron Condor",
        "strikes": {
            "long_put": 465.0,
            "short_put": 482.0,
            "short_call": 558.0,
            "long_call": 575.0,
        },
        "net_credit": 4.50,
        "max_profit": 450.0,
        "max_loss": 1250.0,
    }

    with patch("observability.alerts.send_alert") as mock_send:
        res = dispatch_earnings_crush_alert(candidate)

        assert res["dispatched"] is True
        assert res["level"] == "WARNING"  # Edge >= 1.50 elevates to WARNING
        assert "META" in res["message"]
        assert "1.52x" in res["message"]
        assert "Iron Condor" in res["message"]
        assert "Put Wing: $465.00/$482.00" in res["message"]
        assert "Call Wing: $558.00/$575.00" in res["message"]
        assert "$4.50" in res["message"]
        assert mock_send.call_count == 1


def test_dispatch_earnings_crush_alert_force():
    """force=True dispatches even if crush edge is below threshold."""
    candidate = {
        "symbol": "AMZN",
        "crush_edge_ratio": 1.10,
        "spot": 185.0,
        "earnings_date": "2026-08-25",
    }
    with patch("observability.alerts.send_alert"):
        res = dispatch_earnings_crush_alert(candidate, force=True)
        assert res["dispatched"] is True


# ---------------------------------------------------------------------------
# Delta Hedge Alerting (dispatch_delta_hedge_alert)
# ---------------------------------------------------------------------------

def test_dispatch_delta_hedge_alert_none_input():
    """Passing None preview returns dispatched=False."""
    res = dispatch_delta_hedge_alert(None)
    assert res["dispatched"] is False
    assert "No delta hedge preview" in res["reason"]


def test_dispatch_delta_hedge_alert_within_tolerance():
    """When delta is within tolerance band and action is HOLD, no alert is sent."""
    preview = {
        "symbol": "SPY",
        "beta_weighted_delta_spy": 12.5,
        "target_hedge_shares": -12.5,
        "tolerance_band_shares": 25.0,
        "action": "HOLD",
        "shares": 0.0,
        "required_action": False,
        "reason": "Delta within tolerance band",
    }
    res = dispatch_delta_hedge_alert(preview)
    assert res["dispatched"] is False
    assert "within tolerance band" in res["reason"]


def test_dispatch_delta_hedge_alert_rebalance_sell_spy():
    """When delta exceeds positive tolerance, alert recommends SELL SPY."""
    preview = {
        "symbol": "SPY",
        "net_dollar_delta": 45000.0,
        "beta_weighted_delta_spy": 90.0,
        "target_hedge_shares": -90.0,
        "tolerance_band_shares": 25.0,
        "action": "SELL",
        "shares": 90.0,
        "required_action": True,
        "reason": "Delta imbalance (+90.00 SPY-equiv) exceeds tolerance band (±25.0 shares)",
        "spy_spot": 500.0,
    }

    with patch("observability.alerts.send_alert") as mock_send:
        res = dispatch_delta_hedge_alert(preview)

        assert res["dispatched"] is True
        assert res["level"] == "CRITICAL"  # 90 >= 2 * 25.0 (50) -> CRITICAL
        assert "DELTA HEDGE ALERT" in res["message"]
        assert "+90.00 SPY shares" in res["message"]
        assert "SELL 90 shares SPY" in res["message"]
        assert "$45,000.00" in res["message"]
        assert mock_send.call_count == 1


def test_dispatch_delta_hedge_alert_rebalance_buy_spy():
    """When delta is negative, alert recommends BUY SPY."""
    preview = {
        "symbol": "SPY",
        "net_dollar_delta": -18000.0,
        "beta_weighted_delta_spy": -36.0,
        "target_hedge_shares": 36.0,
        "tolerance_band_shares": 25.0,
        "action": "BUY",
        "shares": 36.0,
        "required_action": True,
        "reason": "Delta imbalance (-36.00 SPY-equiv) exceeds tolerance band",
        "spy_spot": 500.0,
    }

    with patch("observability.alerts.send_alert") as mock_send:
        res = dispatch_delta_hedge_alert(preview)

        assert res["dispatched"] is True
        assert res["level"] == "WARNING"  # 36 < 50 -> WARNING
        assert "BUY 36 shares SPY" in res["message"]
        assert mock_send.call_count == 1


# ---------------------------------------------------------------------------
# General Options Alert Dispatcher (dispatch_options_alert)
# ---------------------------------------------------------------------------

def test_format_options_alert_message_mispricing():
    """format_options_alert_message formats vol_mispricing correctly."""
    level, title, msg = format_options_alert_message("vol_mispricing", {"symbol": "AAPL", "iv_spread": 0.055, "tag": "RICH"})
    assert level == "INFO"
    assert "Volatility Mispricing" in title
    assert "AAPL" in msg
    assert "+0.055" in msg


def test_dispatch_options_alert_custom_type():
    """dispatch_options_alert dispatches test / custom alerts."""
    with patch("observability.alerts.send_alert") as mock_send:
        res = dispatch_options_alert("test_alert", {"status": "all_green"})
        assert res["status"] == "ok"
        assert res["success"] is True
        assert mock_send.call_count == 1

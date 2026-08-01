"""
tests/test_data_api_macro_sentiment.py
=======================================
Tests for api/data_api.py::get_macro_sentiment and get_order_book_ladder.

Covers the fix from a fully hardcoded fixture (fictional CPI/PMI/Employment
categories that this platform never computes) to reading the real macro
telemetry this codebase already tracks (VIX, Sahm Rule, High-Yield OAS,
yield curve, market regime) from output/state_snapshot.json, normalized
against this codebase's own kill-switch/regime thresholds
(dto_models.py::MacroEconomicDTO.killSwitch / market_regime), with a real
trend computed against the most recently rotated prior snapshot.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from settings import settings
import api.data_api as data_api

client = TestClient(data_api.app, client=("127.0.0.1", 54124))


def _write_snapshot(path, **fields) -> None:
    path.write_text(json.dumps(fields), encoding="utf-8")


class TestGetMacroSentiment:
    def test_no_snapshot_degrades_to_empty_with_reason(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        resp = client.get("/data/macro/sentiment")
        assert resp.status_code == 200
        body = resp.json()
        assert body["macro_data"] == []
        assert body["is_synthetic"] is False
        assert body["reason"]

    def test_calm_markets_score_high_with_no_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        _write_snapshot(
            tmp_path / "state_snapshot.json",
            vix=13.0, sahm_rule=0.05, high_yield_oas=3.0, yield_curve=0.4,
            market_regime="RISK ON",
        )
        resp = client.get("/data/macro/sentiment")
        body = resp.json()
        assert body["is_synthetic"] is False
        assert body["reason"] is None
        by_subject = {row["subject"]: row for row in body["macro_data"]}
        assert set(by_subject) == {
            "VIX (Volatility)",
            "Sahm Rule (Recession Signal)",
            "High-Yield OAS (Credit Stress)",
            "Yield Curve (10Y-2Y)",
            "Market Regime",
        }
        for row in body["macro_data"]:
            assert row["value"] > 80, row
            # No prior snapshot to diff against -- never a fabricated up/down.
            assert row["trend"] == "flat", row

    def test_stressed_markets_score_low(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        _write_snapshot(
            tmp_path / "state_snapshot.json",
            vix=32.0, sahm_rule=0.55, high_yield_oas=6.5, yield_curve=-0.4,
            market_regime="RECESSION",
        )
        resp = client.get("/data/macro/sentiment")
        body = resp.json()
        for row in body["macro_data"]:
            assert row["value"] < 5, row

    def test_unrecognized_regime_string_omitted_not_fabricated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        _write_snapshot(
            tmp_path / "state_snapshot.json",
            vix=15.0, sahm_rule=0.1, high_yield_oas=3.5, yield_curve=0.2,
            market_regime="UNKNOWN",
        )
        resp = client.get("/data/macro/sentiment")
        body = resp.json()
        subjects = {row["subject"] for row in body["macro_data"]}
        assert "Market Regime" not in subjects
        assert len(body["macro_data"]) == 4

    def test_trend_reflects_real_history_comparison(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        _write_snapshot(
            history_dir / "state_snapshot_20260101T000000Z.json",
            vix=14.0, sahm_rule=0.1, high_yield_oas=3.5, yield_curve=0.3,
            market_regime="RISK ON",
        )
        _write_snapshot(
            tmp_path / "state_snapshot.json",
            vix=32.0, sahm_rule=0.55, high_yield_oas=6.5, yield_curve=-0.4,
            market_regime="RECESSION",
        )
        resp = client.get("/data/macro/sentiment")
        body = resp.json()
        assert all(row["trend"] == "down" for row in body["macro_data"]), body["macro_data"]

    def test_improving_markets_trend_up(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        _write_snapshot(
            history_dir / "state_snapshot_20260101T000000Z.json",
            vix=25.0, sahm_rule=0.4, high_yield_oas=5.0, yield_curve=-0.1,
            market_regime="NEUTRAL",
        )
        _write_snapshot(
            tmp_path / "state_snapshot.json",
            vix=13.0, sahm_rule=0.05, high_yield_oas=3.0, yield_curve=0.4,
            market_regime="RISK ON",
        )
        resp = client.get("/data/macro/sentiment")
        body = resp.json()
        assert all(row["trend"] == "up" for row in body["macro_data"]), body["macro_data"]

    def test_unchanged_markets_trend_flat(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        fields = dict(vix=15.0, sahm_rule=0.1, high_yield_oas=3.5, yield_curve=0.2, market_regime="RISK ON")
        _write_snapshot(history_dir / "state_snapshot_20260101T000000Z.json", **fields)
        _write_snapshot(tmp_path / "state_snapshot.json", **fields)
        resp = client.get("/data/macro/sentiment")
        body = resp.json()
        assert all(row["trend"] == "flat" for row in body["macro_data"]), body["macro_data"]


class TestGetOrderBookLadder:
    def test_uses_real_quote_when_available(self, monkeypatch):
        class _FakeQuote:
            price = 123.45
            source = "yfinance"
            is_stale = False

        class _FakeProvider:
            def get_latest_quote(self, symbol):
                return _FakeQuote()

        # Patched at the point of use (api.data_api.get_provider, imported
        # via `from data.market_data import ... get_provider` at module
        # top), matching every other endpoint in this file's provider
        # access pattern -- NOT a fresh data.market_data.CompositeProvider(),
        # which get_order_book_ladder deliberately stopped constructing
        # (see its docstring: a fresh instance defeats the provider's own
        # quote TTL cache).
        monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider())
        resp = client.get("/data/ladder/AAPL")
        body = resp.json()
        assert body["current_price"] == 123.45
        assert body["is_synthetic"] is True  # depth ladder itself stays synthetic

    def test_falls_back_to_fixed_price_on_quote_failure(self, monkeypatch):
        class _FailingProvider:
            def get_latest_quote(self, symbol):
                raise RuntimeError("no network")

        monkeypatch.setattr(data_api, "get_provider", lambda: _FailingProvider())
        resp = client.get("/data/ladder/SPY")
        body = resp.json()
        assert body["current_price"] == 450.00
        assert body["is_synthetic"] is True

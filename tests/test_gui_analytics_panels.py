"""
tests/test_gui_analytics_panels.py — PR2 Analytics tab helpers + snapshot threading
====================================================================================
Offline unit tests for the new read-only Analytics panels and the state-snapshot
threading that feeds them. Streamlit ``render_*`` functions can't run outside a
runtime, so we test the pure helpers behind them plus the snapshot contract.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from gui.panels import analytics, analytics_signals


# ── analytics._read_alert_tail (recent-alerts feed) ──────────────────────────

def test_read_alert_tail_parses_and_skips_malformed(tmp_path):
    f = tmp_path / "alerts.jsonl"
    f.write_text(
        '{"timestamp": "2026-07-09T00:00:00Z", "level": "INFO", "message": "a"}\n'
        "not json — should be skipped\n"
        '{"timestamp": "2026-07-09T01:00:00Z", "level": "CRITICAL", "message": "b"}\n',
        encoding="utf-8",
    )
    rows = analytics._read_alert_tail(f, max_lines=50)
    # Newest-first; the malformed middle line is dropped.
    assert {r["message"] for r in rows} == {"a", "b"}
    assert len(rows) == 2
    assert rows[0]["level"] == "CRITICAL"  # "b" (later timestamp) first


def test_read_alert_tail_missing_file_returns_empty(tmp_path):
    assert analytics._read_alert_tail(tmp_path / "nope.jsonl") == []


# ── analytics_signals pure helpers ───────────────────────────────────────────

def test_load_registry_rows_reads_real_registry():
    rows = analytics_signals._load_registry_rows()  # default ml/registry.yaml
    assert rows, "expected the shipped registry to yield rows"
    names = {r.get("model") for r in rows}
    assert any("lgbm" in str(n).lower() for n in names)
    # Registry rows carry the gate metrics the panel renders.
    assert {"role", "trained_date", "cpcv_dsr", "pbo", "deployable"} <= set(rows[0])


def test_load_registry_rows_missing_file_returns_empty():
    assert analytics_signals._load_registry_rows(path="does/not/exist.yaml") == []


def test_sentiment_rows_filters_null_and_keeps_numeric():
    snap = {"signals": [
        {"symbol": "AAPL", "news_sentiment": 0.4},
        {"symbol": "MSFT", "news_sentiment": None},   # skipped
        {"symbol": "XOM"},                            # absent → skipped
    ]}
    rows = analytics_signals._sentiment_rows(snap)
    assert [r["Symbol"] for r in rows] == ["AAPL"]


def test_sentiment_rows_none_snapshot_safe():
    assert analytics_signals._sentiment_rows(None) == []


def test_risk_rows_keeps_partial_skips_both_null():
    snap = {"signals": [
        {"symbol": "AAPL", "realized_slippage": 0.001, "covar_proxy": None},  # kept (partial)
        {"symbol": "MSFT", "realized_slippage": None, "covar_proxy": 0.3},    # kept (partial)
        {"symbol": "XOM", "realized_slippage": None, "covar_proxy": None},    # skipped
    ]}
    syms = {r["Symbol"] for r in analytics_signals._risk_rows(snap)}
    assert syms == {"AAPL", "MSFT"}


# ── snapshot threading: orchestrator writer emits the 3 new per-signal keys ───

def test_orchestrator_snapshot_emits_new_keys(tmp_path, monkeypatch):
    import main_orchestrator
    from settings import settings

    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    df = pd.DataFrame([{
        "Symbol": "AAPL", "Action Signal": "BUY", "Score": 60, "Price": 100.0,
        "Shares": 0.0, "Kelly Target": 0.03,
        "News_Sentiment": 0.42, "Realized Slippage": 0.0012, "CoVaR Proxy": 0.31,
    }])
    main_orchestrator._write_state_snapshot({"market_regime": "RISK ON"}, df, ["AAPL"])

    snap = json.loads((tmp_path / "state_snapshot.json").read_text())
    sig = snap["signals"][0]
    assert sig["news_sentiment"] == pytest.approx(0.42)
    assert sig["realized_slippage"] == pytest.approx(0.0012)
    assert sig["covar_proxy"] == pytest.approx(0.31)


def test_orchestrator_snapshot_absent_metric_is_null_not_zero(tmp_path, monkeypatch):
    """CONSTRAINT #4: a row missing the columns serializes null, never 0.0."""
    import main_orchestrator
    from settings import settings

    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    df = pd.DataFrame([{
        "Symbol": "MSFT", "Action Signal": "HOLD", "Score": 50, "Price": 200.0,
        "Shares": 0.0, "Kelly Target": 0.0,
    }])
    main_orchestrator._write_state_snapshot({"market_regime": "RISK ON"}, df, ["MSFT"])

    sig = json.loads((tmp_path / "state_snapshot.json").read_text())["signals"][0]
    assert sig["news_sentiment"] is None
    assert sig["covar_proxy"] is None

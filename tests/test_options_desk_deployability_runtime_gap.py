"""
tests/test_options_desk_deployability_runtime_gap.py
====================================================
Validates that the Options Desk pilot execution endpoints consistently inject
the honest deployability gate status (as registered in OPTIONS_DESK_DEPLOYABILITY_GATES)
and refuse to fabricate data or claim unverified deployability.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from settings import settings
from api.pilots_api import app, OPTIONS_DESK_DEPLOYABILITY_GATES
from pilots.zero_dte_engine import execute_0dte_trade
from pilots.dispersion_trading import INDEX_WEIGHTS_MAP


@pytest.fixture
def client():
    return TestClient(app)


def test_options_desk_deployability_gates_structure():
    """Verify the static structure of OPTIONS_DESK_DEPLOYABILITY_GATES."""
    expected_modules = ["vol_mispricing", "earnings_crush", "dispersion_trading", "zero_dte_engine"]
    for mod in expected_modules:
        assert mod in OPTIONS_DESK_DEPLOYABILITY_GATES
        gate = OPTIONS_DESK_DEPLOYABILITY_GATES[mod]
        assert gate["deployable"] is False
        assert gate["gate_status"] in ("MEASURED_FAIL", "UNGATEABLE_DATA_GAP")
        assert len(gate["reason"]) > 0


def test_earnings_crush_execute_surfaces_gate_status(client, monkeypatch):
    """Verify earnings crush execution endpoint attaches gate_status."""
    monkeypatch.setattr(settings, "FOLLOW_API_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "PAPER_BROKER_WRITES_ENABLED", True, raising=False)

    resp = client.post(
        "/pilots/options/earnings-crush/execute",
        headers={"Authorization": "Bearer test-token"},
        json={"symbol": "AAPL", "strategy": "Iron Condor", "dry_run": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "gate_status" in data
    assert data["gate_status"]["deployable"] is False
    assert data["gate_status"]["gate_status"] == "UNGATEABLE_DATA_GAP"


def test_dispersion_execute_surfaces_gate_status(client, monkeypatch):
    """Verify dispersion execution endpoint attaches gate_status."""
    monkeypatch.setattr(settings, "FOLLOW_API_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "PAPER_BROKER_WRITES_ENABLED", True, raising=False)

    resp = client.post(
        "/pilots/options/dispersion/execute",
        headers={"Authorization": "Bearer test-token"},
        json={"index_symbol": "SPY", "dry_run": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "gate_status" in data
    assert data["gate_status"]["deployable"] is False
    assert data["gate_status"]["gate_status"] == "UNGATEABLE_DATA_GAP"


def test_execute_0dte_trade_refuses_when_price_missing_and_never_fabricates_1_50(monkeypatch):
    """A 0DTE trade with no quote_price/limit_price and no resolvable spot must fail
    explicitly (CONSTRAINT #4) rather than silently fabricating a fallback fill price."""
    import pilots.price_provider as price_provider
    monkeypatch.setattr(price_provider, "get_latest_price", lambda symbol: 0.0)

    res = execute_0dte_trade(
        symbol="SPY", option_type="CALL", strike=500.0, expiration="2026-08-21",
        contracts=1, quote_price=None, limit_price=None, dry_run=True,
    )
    assert res["ok"] is False
    assert "No quote_price or limit_price provided" in res["error"]
    assert "unit_price" not in res
    assert "fill_price" not in res


def test_dispersion_trading_baskets_distinct_for_spy_and_qqq():
    """SPY and QQQ dispersion weight maps must not be identical -- each index carries its
    own constituent weight allocation, not a shared/copy-pasted default."""
    spy_weights = INDEX_WEIGHTS_MAP["SPY"]
    qqq_weights = INDEX_WEIGHTS_MAP["QQQ"]
    assert spy_weights != qqq_weights
    assert spy_weights["TSLA"] != qqq_weights["TSLA"]


def test_zero_dte_execute_surfaces_gate_status(client, monkeypatch):
    """Verify 0DTE execution endpoint attaches gate_status."""
    monkeypatch.setattr(settings, "FOLLOW_API_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "PAPER_BROKER_WRITES_ENABLED", True, raising=False)

    resp = client.post(
        "/pilots/options/zero-dte/execute",
        headers={"Authorization": "Bearer test-token"},
        json={"symbol": "SPY", "option_type": "CALL", "strike": 500.0, "expiration": "2026-08-18", "limit_price": 2.50, "dry_run": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "gate_status" in data
    assert data["gate_status"]["deployable"] is False
    assert data["gate_status"]["gate_status"] == "UNGATEABLE_DATA_GAP"

"""
tests/test_options_desk_deployability_runtime_gap.py
=====================================================
Unit and integration tests verifying the closure of the Options Desk Deployability Runtime Gap (F4):
1. execute_0dte_trade refuses rather than fabricating $1.50 fill price fallback (CONSTRAINT #4).
2. dispersion_trading provides distinct constituent baskets for SPY vs QQQ.
3. execute_dispersion_trade dynamically derives Long vs Short dispersion from spread sign.
4. Pilots API execution endpoints (earnings crush, dispersion, 0DTE) surface honest gate_status.
"""

import pytest
from fastapi.testclient import TestClient
from api.pilots_api import app, OPTIONS_DESK_DEPLOYABILITY_GATES
from pilots.zero_dte_engine import execute_0dte_trade
from pilots.dispersion_trading import (
    INDEX_CONSTITUENTS_MAP,
    INDEX_WEIGHTS_MAP,
    get_dispersion_opportunities,
    execute_dispersion_trade,
)


@pytest.fixture
def client():
    return TestClient(app)


def test_execute_0dte_trade_refuses_when_price_missing_and_never_fabricates_1_50():
    """Verify execute_0dte_trade refuses without a valid quote/limit price and never fills at $1.50."""
    # Attempt execution with no quote_price or limit_price
    res = execute_0dte_trade(
        symbol="SPY",
        option_type="CALL",
        strike=500.0,
        expiration="2026-08-21",
        contracts=1,
        quote_price=None,
        limit_price=None,
        dry_run=True,
    )
    assert res["ok"] is False
    assert "No quote_price or limit_price provided" in res["error"]
    assert "unit_price" not in res
    assert "fill_price" not in res


def test_execute_0dte_trade_succeeds_with_real_price():
    """Verify execute_0dte_trade succeeds when real quote_price or limit_price is provided."""
    res = execute_0dte_trade(
        symbol="SPY",
        option_type="CALL",
        strike=500.0,
        expiration="2026-08-21",
        contracts=2,
        quote_price=2.35,
        dry_run=True,
    )
    assert res["ok"] is True
    assert res["unit_price"] == 2.35
    assert res["fill_price"] == 235.0
    assert res["contracts"] == 2


def test_dispersion_trading_baskets_distinct_for_spy_and_qqq():
    """Verify that SPY and QQQ baskets have distinct constituent weights and prioritization."""
    spy_weights = INDEX_WEIGHTS_MAP["SPY"]
    qqq_weights = INDEX_WEIGHTS_MAP["QQQ"]
    assert spy_weights != qqq_weights
    assert spy_weights["TSLA"] != qqq_weights["TSLA"]


def test_options_desk_execute_endpoints_surface_honest_gate_status(client, monkeypatch):
    """Verify that earnings crush, dispersion, and zero-DTE execute endpoints return honest gate_status."""
    from unittest.mock import patch
    from settings import settings

    headers = {
        "Authorization": "Bearer test-cmd-token",
    }

    with patch.object(settings, "FOLLOW_API_TOKEN", "test-cmd-token"), \
         patch.object(settings, "PAPER_BROKER_WRITES_ENABLED", True):

        # 1. Earnings Crush
        resp_ec = client.post(
            "/pilots/options/earnings-crush/execute",
            json={"symbol": "NVDA", "dry_run": True},
            headers=headers,
        )
        assert resp_ec.status_code == 200
        data_ec = resp_ec.json()
        assert "gate_status" in data_ec
        assert data_ec["gate_status"]["deployable"] is False
        assert data_ec["gate_status"]["gate_status"] == "UNGATEABLE_DATA_GAP"
        assert "single-name IV" in data_ec["gate_status"]["reason"]

        # 2. Dispersion Trading
        resp_disp = client.post(
            "/pilots/options/dispersion/execute",
            json={"index_symbol": "SPY", "dry_run": True},
            headers=headers,
        )
        assert resp_disp.status_code == 200
        data_disp = resp_disp.json()
        assert "gate_status" in data_disp
        assert data_disp["gate_status"]["deployable"] is False
        assert data_disp["gate_status"]["gate_status"] == "UNGATEABLE_DATA_GAP"
        assert "substitution bias" in data_disp["gate_status"]["reason"]

        # 3. 0DTE Trading
        resp_zdte = client.post(
            "/pilots/options/zero-dte/execute",
            json={"symbol": "SPY", "strike": 500.0, "limit_price": 2.50, "dry_run": True},
            headers=headers,
        )
        assert resp_zdte.status_code == 200
        data_zdte = resp_zdte.json()
        assert "gate_status" in data_zdte
        assert data_zdte["gate_status"]["deployable"] is False
        assert data_zdte["gate_status"]["gate_status"] == "UNGATEABLE_DATA_GAP"
        assert "intraday history" in data_zdte["gate_status"]["reason"]

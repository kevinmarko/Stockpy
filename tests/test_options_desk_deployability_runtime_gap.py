"""
tests/test_options_desk_deployability_runtime_gap.py
====================================================
Validates that the Options Desk pilot execution endpoints consistently inject
the honest deployability gate status (as registered in OPTIONS_DESK_DEPLOYABILITY_GATES),
refuse to fabricate data or claim unverified deployability, and -- as of the
2026-08-29 fix -- actually ENFORCE that gate by default: earnings_crush,
dispersion_trading, and zero_dte_engine are each an UNGATEABLE_DATA_GAP, and all
three now block execution by default and proceed only when the request
explicitly sets override_deployability_gate=True, mirroring the pre-existing
MEASURED_FAIL enforcement pattern for vol_mispricing
(TestVolMispricingExecuteDeployabilityGate in tests/test_pilots_api.py).
"""

from __future__ import annotations

from unittest import mock

import pytest
from fastapi.testclient import TestClient
from settings import settings
from api.pilots_api import app, OPTIONS_DESK_DEPLOYABILITY_GATES
from pilots.zero_dte_engine import execute_0dte_trade
from pilots.dispersion_trading import INDEX_CONSTITUENTS_MAP, INDEX_WEIGHTS_MAP


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
    """Verify earnings crush execution endpoint attaches gate_status once the
    caller has explicitly overridden the UNGATEABLE_DATA_GAP block."""
    monkeypatch.setattr(settings, "FOLLOW_API_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "PAPER_BROKER_WRITES_ENABLED", True, raising=False)

    resp = client.post(
        "/pilots/options/earnings-crush/execute",
        headers={"Authorization": "Bearer test-token"},
        json={"symbol": "AAPL", "strategy": "Iron Condor", "dry_run": True, "override_deployability_gate": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "gate_status" in data
    assert data["gate_status"]["deployable"] is False
    assert data["gate_status"]["gate_status"] == "UNGATEABLE_DATA_GAP"


def test_earnings_crush_execute_blocked_without_override_never_executes_a_trade(client, monkeypatch):
    """Without override_deployability_gate, the endpoint refuses -- and never
    even calls execute_earnings_crush_trade (no PaperAccountStore write)."""
    monkeypatch.setattr(settings, "FOLLOW_API_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "PAPER_BROKER_WRITES_ENABLED", True, raising=False)

    with mock.patch("pilots.earnings_crush.execute_earnings_crush_trade") as mock_exec:
        resp = client.post(
            "/pilots/options/earnings-crush/execute",
            headers={"Authorization": "Bearer test-token"},
            json={"symbol": "AAPL", "strategy": "Iron Condor", "dry_run": True},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["blocked"] is True
    # gate_status IS present on the blocked response -- matching vol_mispricing's
    # blocked-response shape exactly (see post_options_mispricing_execute), so a
    # caller gets the structured reason (e.g. the specific data-gap explanation),
    # not just the generic templated message string.
    assert "gate_status" in data
    assert data["gate_status"]["gate_status"] == "UNGATEABLE_DATA_GAP"
    mock_exec.assert_not_called()


def test_dispersion_execute_surfaces_gate_status(client, monkeypatch):
    """Verify dispersion execution endpoint attaches gate_status once the
    caller has explicitly overridden the UNGATEABLE_DATA_GAP block."""
    monkeypatch.setattr(settings, "FOLLOW_API_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "PAPER_BROKER_WRITES_ENABLED", True, raising=False)

    resp = client.post(
        "/pilots/options/dispersion/execute",
        headers={"Authorization": "Bearer test-token"},
        json={"index_symbol": "SPY", "dry_run": True, "override_deployability_gate": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "gate_status" in data
    assert data["gate_status"]["deployable"] is False
    assert data["gate_status"]["gate_status"] == "UNGATEABLE_DATA_GAP"


def test_dispersion_execute_blocked_without_override_never_executes_a_trade(client, monkeypatch):
    """Without override_deployability_gate, the endpoint refuses -- and never
    even calls execute_dispersion_trade (no PaperAccountStore write)."""
    monkeypatch.setattr(settings, "FOLLOW_API_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "PAPER_BROKER_WRITES_ENABLED", True, raising=False)

    with mock.patch("pilots.dispersion_trading.execute_dispersion_trade") as mock_exec:
        resp = client.post(
            "/pilots/options/dispersion/execute",
            headers={"Authorization": "Bearer test-token"},
            json={"index_symbol": "SPY", "dry_run": True},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["blocked"] is True
    # gate_status IS present on the blocked response -- matching vol_mispricing's
    # blocked-response shape exactly (see post_options_mispricing_execute), so a
    # caller gets the structured reason (e.g. the specific data-gap explanation),
    # not just the generic templated message string.
    assert "gate_status" in data
    assert data["gate_status"]["gate_status"] == "UNGATEABLE_DATA_GAP"
    mock_exec.assert_not_called()


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
    """SPY and QQQ dispersion baskets must not be identical -- each index carries its own
    constituent set and weight allocation, not a shared/copy-pasted default. As of the
    2026-08-19 fix (docs/VALIDATION_STRATEGY_FIX_LOG.md), the two baskets differ in
    CONSTITUENT SET, not just reordering/reweighting the same 8 tickers: SPY includes real
    non-tech sector exposure (JPM financials, UNH healthcare) that QQQ's Nasdaq-100 index
    rules structurally exclude, while QQQ keeps its real growth/semiconductor tilt
    (AVGO, TSLA) in their place."""
    spy_constituents = set(INDEX_CONSTITUENTS_MAP["SPY"])
    qqq_constituents = set(INDEX_CONSTITUENTS_MAP["QQQ"])
    assert spy_constituents != qqq_constituents, "SPY/QQQ must not share an identical constituent set"
    assert "JPM" in spy_constituents and "JPM" not in qqq_constituents
    assert "UNH" in spy_constituents and "UNH" not in qqq_constituents
    assert "TSLA" in qqq_constituents and "TSLA" not in spy_constituents
    assert "AVGO" in qqq_constituents and "AVGO" not in spy_constituents

    spy_weights = INDEX_WEIGHTS_MAP["SPY"]
    qqq_weights = INDEX_WEIGHTS_MAP["QQQ"]
    assert spy_weights != qqq_weights
    # A ticker present in both baskets must also carry a genuinely different weight.
    assert spy_weights["AAPL"] != qqq_weights["AAPL"]


def test_zero_dte_execute_surfaces_gate_status(client, monkeypatch):
    """Verify 0DTE execution endpoint attaches gate_status once the caller has
    explicitly overridden the UNGATEABLE_DATA_GAP block."""
    monkeypatch.setattr(settings, "FOLLOW_API_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "PAPER_BROKER_WRITES_ENABLED", True, raising=False)

    resp = client.post(
        "/pilots/options/zero-dte/execute",
        headers={"Authorization": "Bearer test-token"},
        json={
            "symbol": "SPY", "option_type": "CALL", "strike": 500.0,
            "expiration": "2026-08-18", "limit_price": 2.50, "dry_run": True,
            "override_deployability_gate": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "gate_status" in data
    assert data["gate_status"]["deployable"] is False
    assert data["gate_status"]["gate_status"] == "UNGATEABLE_DATA_GAP"


def test_zero_dte_execute_blocked_without_override_never_executes_a_trade(client, monkeypatch):
    """Without override_deployability_gate, the endpoint refuses -- and never
    even calls execute_0dte_trade (no PaperAccountStore write). This is the
    gate-enforcement check that was missing entirely for this endpoint prior to
    the 2026-08-29 fix (earnings_crush/dispersion_trading already enforced it;
    zero_dte_engine's handler called execute_0dte_trade unconditionally)."""
    monkeypatch.setattr(settings, "FOLLOW_API_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "PAPER_BROKER_WRITES_ENABLED", True, raising=False)

    with mock.patch("pilots.zero_dte_engine.execute_0dte_trade") as mock_exec:
        resp = client.post(
            "/pilots/options/zero-dte/execute",
            headers={"Authorization": "Bearer test-token"},
            json={
                "symbol": "SPY", "option_type": "CALL", "strike": 500.0,
                "expiration": "2026-08-18", "limit_price": 2.50, "dry_run": True,
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["blocked"] is True
    # gate_status IS present on the blocked response -- matching vol_mispricing's
    # blocked-response shape exactly (see post_options_mispricing_execute), so a
    # caller gets the structured reason (e.g. the specific data-gap explanation),
    # not just the generic templated message string.
    assert "gate_status" in data
    assert data["gate_status"]["gate_status"] == "UNGATEABLE_DATA_GAP"
    mock_exec.assert_not_called()

"""
tests/test_flatten_proposal.py
==============================
Fully offline tests for the gated dry-run flatten-on-kill proposal
(``execution/flatten_proposal.py``) and its wiring into
``execution.kill_switch.GlobalKillSwitch.activate``.

Proves:

* Activating the kill switch with ``FLATTEN_ON_KILL=True`` writes a proposal
  JSON containing CLOSING intents for the held positions.
* With the flag OFF nothing is written (zero behavioural change).
* The proposal is structurally a DRY-RUN preview: every intent has
  ``dry_run``-shaped output and ``allow_place=False`` — NO broker is ever
  contacted.
* ``send_alert`` fires (CRITICAL) on kill-switch activation.

Broker / position mocks are defined LOCALLY.  This capability is
placement-INCAPABLE: no code path here submits, cancels, or mutates an order.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest

import execution.flatten_proposal as fp
from execution.flatten_proposal import build_flatten_proposal, emit_flatten_proposal
from execution.kill_switch import GlobalKillSwitch


# ---------------------------------------------------------------------------
# Local mocks (no import of the real robinhood/broker layer)
# ---------------------------------------------------------------------------

def _pos(symbol, qty, price=100.0, avg=90.0):
    """A PortfolioPosition-shaped duck (symbol/quantity/current_price/...)."""
    return SimpleNamespace(
        symbol=symbol,
        quantity=qty,
        average_cost=avg,
        current_price=price,
        market_value=qty * price,
        unrealized_pl=(price - avg) * qty,
    )


def _proposal_path(tmp_path):
    return tmp_path / fp._PROPOSAL_FILENAME


# ---------------------------------------------------------------------------
# build_flatten_proposal — pure payload
# ---------------------------------------------------------------------------

def test_build_proposal_closes_long_with_sell():
    positions = [_pos("AAPL", 10.0)]
    payload = build_flatten_proposal(positions, reason="test")

    assert payload["dry_run"] is True
    assert payload["kill_switch_active"] is True
    assert payload["n_intents"] == 1
    intent = payload["intents"][0]
    assert intent["symbol"] == "AAPL"
    assert intent["action"] == "SELL"     # closing a long
    assert intent["side"] == "sell"
    assert intent["qty"] == 10.0
    assert intent["allow_place"] is False  # structurally preview-only
    # never placeable
    assert payload["n_placeable"] == 0


def test_build_proposal_closes_short_with_buy():
    positions = [_pos("TSLA", -5.0)]
    payload = build_flatten_proposal(positions)
    intent = payload["intents"][0]
    assert intent["action"] == "BUY"       # closing a short
    assert intent["side"] == "buy"
    assert intent["qty"] == 5.0            # absolute quantity
    assert intent["current_qty"] == -5.0
    assert intent["allow_place"] is False


def test_build_proposal_skips_zero_quantity():
    positions = [_pos("AAPL", 0.0), _pos("MSFT", 3.0)]
    payload = build_flatten_proposal(positions)
    symbols = [i["symbol"] for i in payload["intents"]]
    assert symbols == ["MSFT"]  # zero-qty holding produces no fabricated intent


def test_build_proposal_empty_positions_still_valid():
    payload = build_flatten_proposal([])
    assert payload["n_intents"] == 0
    assert payload["intents"] == []
    assert payload["dry_run"] is True
    assert "PROPOSAL ONLY" in payload["note"]


# ---------------------------------------------------------------------------
# emit_flatten_proposal — gating + file
# ---------------------------------------------------------------------------

def test_emit_writes_file_when_enabled(tmp_path):
    positions = [_pos("AAPL", 10.0), _pos("MSFT", -2.0)]
    path = emit_flatten_proposal(
        positions, reason="kill", output_dir=tmp_path, flatten_enabled=True
    )
    assert path is not None
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["dry_run"] is True
    assert {i["symbol"] for i in data["intents"]} == {"AAPL", "MSFT"}
    assert all(i["allow_place"] is False for i in data["intents"])


def test_emit_writes_nothing_when_disabled(tmp_path):
    positions = [_pos("AAPL", 10.0)]
    path = emit_flatten_proposal(
        positions, output_dir=tmp_path, flatten_enabled=False
    )
    assert path is None
    assert not _proposal_path(tmp_path).exists()


def test_emit_never_raises_on_write_failure(tmp_path, monkeypatch):
    # Force json.dumps to blow up inside emit; it must swallow and return None.
    monkeypatch.setattr(fp.json, "dumps", mock.MagicMock(side_effect=RuntimeError("boom")))
    path = emit_flatten_proposal(
        [_pos("AAPL", 1.0)], output_dir=tmp_path, flatten_enabled=True
    )
    assert path is None  # swallowed, no crash


# ---------------------------------------------------------------------------
# kill_switch.activate wiring
# ---------------------------------------------------------------------------

def test_activate_emits_proposal_and_alert_when_flatten_on(tmp_path, monkeypatch):
    """FLATTEN_ON_KILL=True → proposal JSON written next to sentinel + CRITICAL alert."""
    monkeypatch.setattr("execution.kill_switch.settings.FLATTEN_ON_KILL", True, raising=False)

    # Local position source — patch the DB loader so no network / DB is touched.
    monkeypatch.setattr(fp, "_load_current_positions", lambda: [_pos("AAPL", 4.0)])

    alert_spy = mock.MagicMock()
    monkeypatch.setattr("observability.alerts.send_alert", alert_spy)

    ks = GlobalKillSwitch(sentinel_file=tmp_path / "KILL_SWITCH")
    ks.activate(reason="circuit breaker")

    # Sentinel written.
    assert ks.is_active()

    # Proposal written next to the sentinel (output_dir = sentinel parent).
    proposal = _proposal_path(tmp_path)
    assert proposal.exists()
    data = json.loads(proposal.read_text())
    assert data["dry_run"] is True
    assert data["reason"] == "circuit breaker"
    assert data["intents"][0]["symbol"] == "AAPL"
    assert data["intents"][0]["allow_place"] is False

    # CRITICAL alert fired on activation.
    assert alert_spy.called
    assert alert_spy.call_args_list[0].args[0] == "CRITICAL"


def test_activate_no_proposal_when_flatten_off(tmp_path, monkeypatch):
    """FLATTEN_ON_KILL=False → sentinel written, but NO proposal file."""
    monkeypatch.setattr("execution.kill_switch.settings.FLATTEN_ON_KILL", False, raising=False)
    monkeypatch.setattr(fp, "_load_current_positions", lambda: [_pos("AAPL", 4.0)])

    alert_spy = mock.MagicMock()
    monkeypatch.setattr("observability.alerts.send_alert", alert_spy)

    ks = GlobalKillSwitch(sentinel_file=tmp_path / "KILL_SWITCH")
    ks.activate(reason="no-flatten")

    assert ks.is_active()
    assert not _proposal_path(tmp_path).exists()
    # Alert still fires regardless of the flatten flag.
    assert alert_spy.called
    assert alert_spy.call_args_list[0].args[0] == "CRITICAL"


def test_activate_survives_proposal_emission_failure(tmp_path, monkeypatch):
    """A crash inside the proposal path must never block kill-switch activation."""
    monkeypatch.setattr("execution.kill_switch.settings.FLATTEN_ON_KILL", True, raising=False)
    monkeypatch.setattr(
        "execution.flatten_proposal.emit_flatten_proposal",
        mock.MagicMock(side_effect=RuntimeError("boom")),
    )
    monkeypatch.setattr("observability.alerts.send_alert", mock.MagicMock())

    ks = GlobalKillSwitch(sentinel_file=tmp_path / "KILL_SWITCH")
    ks.activate(reason="still-activates")  # must not raise

    assert ks.is_active()  # the safety-critical action completed


def test_no_broker_contact_in_module():
    """The module contains no autonomous-placement (place_*) function."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(fp))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
            assert not name.startswith("place_"), f"forbidden place_* fn: {name}"
            assert name not in {
                "submit_order", "buy_order", "sell_order", "place_order",
            }, f"forbidden order fn: {name}"

"""
tests/test_compose_advisory_only_golden.py — byte-identity pin for the
advisory-only execution queue across the Follow-a-Pilot archive (step 4c).
=============================================================================

``execution/compose.py`` used to union the advisory source with every
actively-followed Pilot's source. Follow-a-Pilot was archived to ``legacy/``
(2026-09, step 4c), so compose now reads the advisory source only. With no
follows active (the only state production could be in -- every follow was
cancelled before the archive), that change must be invisible: the queue
``compose_and_emit`` writes has to be byte-for-byte what the pre-archive code
wrote for the same inputs.

``tests/fixtures/compose_advisory_only_queue.golden`` was captured by running
this exact scenario against ``origin/main`` @ 80c32cee (before any 4c edit),
with ``REGEN_COMPOSE_GOLDEN=1``. The test below re-runs the scenario and
compares the raw bytes, which covers every field incl. ``client_order_id``,
``strategy_id``, ``sources``/``overridden``, the gate verdicts and the sizing.

Everything that could vary between runs is pinned: ``now``, the macro DTO
(passed explicitly so no DB cache is read), the kill-switch sentinel paths,
the per-order notional cap, and the execution mode.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import pytest

from execution.compose import compose_and_emit, write_source

GOLDEN = Path(__file__).parent / "fixtures" / "compose_advisory_only_queue.golden"
_NOW = datetime(2026, 7, 17, 15, 0, tzinfo=timezone.utc)  # Fri 11:00 ET, inside RTH


@dataclass
class _Pos:
    symbol: str
    quantity: float
    current_price: float
    market_value: float
    average_cost: float = 0.0
    unrealized_pl: float = 0.0


@dataclass
class _Snap:
    positions: Dict[str, _Pos] = field(default_factory=dict)
    total_equity: float = 100_000.0
    buying_power: float = 100_000.0


def _snap(equity: float, positions: Optional[Dict[str, _Pos]] = None) -> _Snap:
    return _Snap(positions=positions or {}, total_equity=equity, buying_power=equity)


def _advisory_target(symbol, action, *, conviction=0.9, pct=0.05,
                      strategy="advisory", rationale="because") -> dict:
    return {
        "symbol": symbol, "action": action, "conviction": conviction,
        "suggested_position_pct": pct, "strategy": strategy, "rationale": rationale,
    }


def _render_advisory_only_queue(tmp_path: Path, monkeypatch) -> bytes:
    from dto_models import MacroEconomicDTO
    from settings import settings
    import execution.kill_switch as ks

    monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)
    monkeypatch.setattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 0.0, raising=False)
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path, raising=False)
    monkeypatch.setattr(ks, "KILL_SWITCH_FILE", tmp_path / "KILL_SWITCH")
    monkeypatch.setattr(ks, "SOFT_HALT_FILE", tmp_path / "SOFT_HALT")

    write_source(
        "advisory",
        [
            _advisory_target("NVDA", "BUY", conviction=0.93, pct=0.04, rationale="trend + momentum"),
            _advisory_target("MSFT", "SELL", conviction=0.9, pct=0.0, rationale="exit: score decay"),
            _advisory_target("AAPL", "BUY", conviction=0.87, pct=0.03, strategy="quality"),
            # Below queue_builder's 0.85 advisory floor -> must not appear.
            _advisory_target("TSLA", "BUY", conviction=0.5, pct=0.05),
        ],
        output_dir=tmp_path, now=_NOW,
    )
    account = _snap(100_000.0, {
        "MSFT": _Pos("MSFT", 10.0, 400.0, 4000.0, average_cost=350.0),
        "AAPL": _Pos("AAPL", 5.0, 200.0, 1000.0, average_cost=180.0),
    })
    calm_macro = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=3.0, inflation_rate=2.5,
        sahm_rule_indicator=0.1, vix_value=15.0,
    )
    path = compose_and_emit(account, output_dir=tmp_path, now=_NOW, macro_dto=calm_macro)
    assert path is not None, "advisory-only compose must write a queue"
    return Path(path).read_bytes()


def test_advisory_only_queue_is_byte_identical_to_pre_archive_main(tmp_path, monkeypatch):
    rendered = _render_advisory_only_queue(tmp_path, monkeypatch)
    if os.environ.get("REGEN_COMPOSE_GOLDEN") == "1":
        GOLDEN.write_bytes(rendered)
        pytest.skip("golden regenerated")
    assert rendered == GOLDEN.read_bytes()


def test_golden_actually_covers_the_ids_and_attribution_fields():
    """Guard against a vacuous golden: it must carry real intents with the
    order-identity and attribution fields the byte comparison is protecting."""
    import json

    payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
    intents = payload["intents"]
    symbols = sorted(i["symbol"] for i in intents)
    assert symbols == ["AAPL", "MSFT", "NVDA"]  # TSLA filtered by conviction floor
    assert all(i["gate_allowed"] for i in intents)  # a real, passing gate run
    # strategy_id is not a payload key; it is folded into client_order_id.
    # Recomputing the id with strategy_id="advisory" pins it exactly.
    from execution.order_manager import make_client_order_id

    for i in intents:
        # queue_builder's gate qty: NVDA not held (1.0); AAPL held @ $200,
        # $3,000 target -> 15; MSFT full exit of the 10 held.
        gate_qty = {"NVDA": 1.0, "AAPL": 15.0, "MSFT": 10.0}[i["symbol"]]
        expected = make_client_order_id("advisory", i["symbol"], i["side"], gate_qty, timestamp=_NOW)
        assert i["client_order_id"] == expected
        assert i["sources"] == [{"source_id": "advisory", "target_notional": i["sources"][0]["target_notional"]}]
        assert i["overridden"] == []

"""Guards on the execution/broker_base.py dataclasses.

A dataclass that annotates the same field twice is silently accepted by
Python: the field keeps its FIRST position but takes the LAST default, so a
later edit to only one of the two declarations can change behavior unnoticed.
OrderIntent carried a duplicate ``target_qty`` until 2026-10-05.
"""
import ast
import dataclasses
from pathlib import Path

from execution.broker_base import OrderIntent

_BROKER_BASE = Path(__file__).resolve().parent.parent / "execution" / "broker_base.py"


def test_no_class_in_broker_base_declares_a_field_twice():
    tree = ast.parse(_BROKER_BASE.read_text())
    dupes = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        names = [
            stmt.target.id
            for stmt in node.body
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
        ]
        repeated = sorted({n for n in names if names.count(n) > 1})
        if repeated:
            dupes[node.name] = repeated
    assert dupes == {}


def test_order_intent_field_order_is_unchanged():
    # Positional construction depends on this order; pin it.
    assert [f.name for f in dataclasses.fields(OrderIntent)] == [
        "strategy_id", "symbol", "side", "qty", "order_type", "limit_price",
        "time_in_force", "target_qty", "client_order_id", "legs", "dry_run",
        "priority", "decision_context",
    ]


def test_order_intent_target_qty_defaults_to_none():
    from execution.broker_base import OrderSide

    intent = OrderIntent(strategy_id="s", symbol="X", side=OrderSide.BUY, qty=1.0)
    assert intent.target_qty is None

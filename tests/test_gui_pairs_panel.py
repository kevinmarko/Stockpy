"""
tests/test_gui_pairs_panel.py — PR3 Pairs analytics panel (advisory-only)
=========================================================================
Offline unit tests for the read-only Pairs tab. Streamlit render code can't run
outside a runtime, so we test the pure helpers behind it plus the invariant that
this ADVISORY-ONLY panel contains NO order/execution code.
"""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd

from gui.panels.pairs import _signal_label, _align_closes


# ── _signal_label — the current-state translator ─────────────────────────────

def test_signal_label_insufficient_history():
    assert "insufficient history" in _signal_label(0.0, float("nan"), 0.05)


def test_signal_label_cointegration_broken():
    # rolling ADF p above the exit threshold → not tradeable.
    assert "not cointegrated" in _signal_label(0.0, 3.0, 0.20)


def test_signal_label_flat_entries():
    assert _signal_label(0.0, -2.5, 0.02) == "Entry LONG spread (long Y / short X)"
    assert _signal_label(0.0, 2.5, 0.02) == "Entry SHORT spread (short Y / long X)"
    assert "flat" in _signal_label(0.0, 0.5, 0.02)  # below entry threshold


def test_signal_label_holds_and_exits():
    assert "Hold LONG" in _signal_label(1.0, -1.5, 0.02)
    assert "Hold SHORT" in _signal_label(-1.0, 1.5, 0.02)
    # long spread, z crossed back to >= 0 → exit
    assert "Exit" in _signal_label(1.0, 0.1, 0.02)


def test_signal_label_stop_loss():
    assert "STOP" in _signal_label(1.0, -4.5, 0.02)
    assert "STOP" in _signal_label(-1.0, 4.5, 0.02)


# ── _align_closes — inner join on common dates ───────────────────────────────

def test_align_closes_inner_join_drops_nonoverlap_and_empty():
    idx_a = pd.date_range("2025-01-01", periods=5, freq="D")
    idx_b = pd.date_range("2025-01-03", periods=5, freq="D")  # overlaps 01-03..01-05
    series = {
        "AAA": pd.Series(np.arange(5.0), index=idx_a),
        "BBB": pd.Series(np.arange(5.0), index=idx_b),
        "CCC": pd.Series(dtype=float),  # empty → dropped
    }
    df = _align_closes(series)
    assert list(df.columns) == ["AAA", "BBB"]         # empty column dropped
    assert len(df) == 3                               # only the 3 overlapping dates
    assert not df.isna().any().any()                  # inner join, no NaN


def test_align_closes_empty_input_safe():
    assert _align_closes({}).empty


# ── advisory-only invariant: NO order/execution code ─────────────────────────

def test_pairs_panel_has_no_order_functions():
    """The Pairs tab is advisory display only — it must define no order/
    execution functions (mirrors the repo's no-order-functions guard)."""
    src = Path("gui/panels/pairs.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned_exact = {
        "submit_order", "buy_order", "sell_order", "place_order",
        "place_equity_order", "place_option_order",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.name not in banned_exact, f"order fn found: {node.name}"
            assert not node.name.startswith("place_"), f"place_* fn found: {node.name}"

"""Regression guard: pipeline/production_steps.py must never label a
synthetic, non-trade price window as a real post-trade evaluation.

A block used to compute an "edge ratio" over a fictional 15-bars-ago ->
today window (no real TransactionsStore trade backing it) and append it to
the user-facing "Strategy Explainer Notes" as "POST-TRADE EDGE RATIO" --
misleading, since no trade occurred, and redundant, since the real
MAE/MFE/Edge Ratio (computed from an actual trade's genuine intra-trade OHLC
path) are written by evaluation_engine.EvaluationEngine.evaluate_portfolio()
later in the same step and unconditionally overwrite it. See CLAUDE.md /
docs/signals -- MAE/MFE must come from a real intra-trade path, never a
fabricated stand-in.
"""
from pathlib import Path


def test_no_post_trade_edge_ratio_placeholder_text():
    src = Path("pipeline/production_steps.py").read_text(encoding="utf-8")
    assert "POST-TRADE EDGE RATIO" not in src, (
        "pipeline/production_steps.py must not label a synthetic, non-trade "
        "price window as a real post-trade evaluation in user-facing notes."
    )
    assert "calculate_edge_ratio(history_df, trade_entry_p, entry_d, exit_d)" not in src, (
        "the fictional 15-bars-ago pseudo-trade edge-ratio computation should "
        "not be reintroduced; the real MAE/MFE/Edge Ratio come from "
        "EvaluationEngine.evaluate_portfolio()'s genuine TransactionsStore-backed "
        "computation later in the same pipeline step."
    )

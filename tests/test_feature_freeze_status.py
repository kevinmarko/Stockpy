"""Tests for scripts/feature_freeze_status.py (step 7 freeze progress)."""
from types import SimpleNamespace

import main_orchestrator
from scripts import feature_freeze_status as ffs


def _t(strategy_id, pnl):
    return {"strategy_id": strategy_id, "realized_pnl": pnl}


def test_constant_matches_the_executor():
    assert ffs.PIPELINE_STRATEGY_ID == main_orchestrator.PIPELINE_STRATEGY_ID


def test_only_pipeline_trades_count():
    closed = [_t("main_pipeline", 5.0), _t("Manual Trade", 100.0),
              _t("Delta Hedge", -3.0), _t("main_pipeline", -2.0)]
    s = ffs.summarize(closed, [])
    assert s["closed_pipeline_trades"] == 2
    assert s["win_rate"] == 0.5
    assert s["total_realized_pnl"] == 3.0
    assert s["freeze_can_end"] is False


def test_threshold_reached():
    s = ffs.summarize([_t("main_pipeline", 1.0)] * 30, [])
    assert s["freeze_can_end"] is True


def test_empty_history_reports_nulls_not_zeros():
    s = ffs.summarize([], [])
    assert s["closed_pipeline_trades"] == 0
    assert s["win_rate"] is None and s["total_realized_pnl"] is None


def test_unmeasured_pnl_is_counted_but_excluded():
    s = ffs.summarize([_t("main_pipeline", None), _t("main_pipeline", 4.0)], [])
    assert s["closed_pipeline_trades"] == 2
    assert s["unmeasured_pnl_trades"] == 1
    assert s["win_rate"] == 1.0


def test_open_positions_filtered_to_pipeline():
    pos = [SimpleNamespace(symbol="AGNC", strategy_id="main_pipeline"),
           SimpleNamespace(symbol="ABR", strategy_id="Manual Trade")]
    assert ffs.summarize([], pos)["open_pipeline_positions"] == ["AGNC"]

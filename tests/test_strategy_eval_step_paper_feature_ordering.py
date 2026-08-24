"""
tests/test_strategy_eval_step_paper_feature_ordering.py
=========================================================
PR 872 remediation (Agent 5, Task 3): ``populate_live_paper_features`` used
to be a train/serve-skew no-op inside
``pipeline/production_steps.py::StrategyEvalStep.run`` for THREE compounding
reasons:

1. It ran AFTER ``global_registry.run_pre_compute(ctx.dashboard_df, ...)`` --
   the real serve-time consumer (``signals/lgbm_ranker.py``'s
   ``LGBMRankerSignal.pre_compute`` reads ``ctx.dashboard_df`` directly via
   ``build_pit_feature_matrix``) -- so the six ``paper_*`` columns did not
   exist yet when the one caller that matters most read them.
2. It ran AFTER ``pit_df = ctx.dashboard_df.copy()`` -- the PIT-snapshot
   training-panel writer's own copy -- so even the disk-persisted snapshot
   never saw the columns either.
3. It was gated behind ``settings.PIT_CAPTURE_ENABLED``, a flag that (per
   its own ``settings.py`` description) controls only whether TODAY's PIT
   snapshot is written to disk for future retrains -- unrelated to whether
   live inference sees the paper_* features this cycle.

Two things are proven here:

* ``TestPaperFeatureCallOrdering`` -- an AST-based structural check (same
  technique as ``tests/test_symbol_rating_wiring.py``, deliberately NOT
  calling ``StrategyEvalStep.run()`` end-to-end, which imports
  ``main_orchestrator`` and its full heavy engine chain) confirming the
  ``populate_live_paper_features(...)`` call site now sits BEFORE both
  ``global_registry.run_pre_compute(...)`` and
  ``pit_df = ctx.dashboard_df.copy()`` in source order, and is NOT nested
  inside the ``if settings.PIT_CAPTURE_ENABLED:`` block.
* ``TestPopulateLivePaperFeaturesDataDriven`` -- a real, data-driven proof
  that ``ml.training_data.populate_live_paper_features`` (the function being
  ordering-fixed) actually writes non-NaN values into a ``dashboard_df``
  given real paper-order/closed-trade fixture history, and that its output
  matches ``_pit_ticker_row``'s independent computation for the same
  symbol/date exactly -- proving the de-duplication refactor
  (``_paper_features_for_symbol``) didn't silently diverge the two paths.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import numpy as np
import pandas as pd
import pytest

import pipeline.production_steps as ps_mod
from ml.training_data import _pit_ticker_row, populate_live_paper_features


# ---------------------------------------------------------------------------
# AST-based ordering check
# ---------------------------------------------------------------------------
class TestPaperFeatureCallOrdering:
    def _parse_run_body(self):
        source = textwrap.dedent(inspect.getsource(ps_mod.StrategyEvalStep.run))
        tree = ast.parse(source)
        func_node = tree.body[0]
        assert isinstance(func_node, ast.FunctionDef)
        return func_node

    def _find_call_lineno(self, func_node: ast.FunctionDef, func_name: str) -> int:
        """Returns the lineno of the first Call whose func resolves to
        `func_name` (matches a bare Name call like `populate_live_paper_features(...)`
        or an attribute call like `global_registry.run_pre_compute(...)`)."""
        for node in ast.walk(func_node):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == func_name:
                return node.lineno
            if isinstance(fn, ast.Attribute) and fn.attr == func_name:
                return node.lineno
        raise AssertionError(f"No call to {func_name!r} found in StrategyEvalStep.run")

    def _find_assign_lineno(self, func_node: ast.FunctionDef, target_name: str) -> int:
        """Returns the lineno of the first top-level (or nested) Assign whose
        target is `ctx.dashboard_df.copy()` bound to `target_name`."""
        for node in ast.walk(func_node):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            if node.targets[0].id != target_name:
                continue
            value = node.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "copy"
            ):
                return node.lineno
        raise AssertionError(f"No `{target_name} = ....copy()` assignment found")

    def _find_pit_capture_if(self, func_node: ast.FunctionDef) -> ast.If:
        for node in ast.walk(func_node):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            if isinstance(test, ast.Attribute) and test.attr == "PIT_CAPTURE_ENABLED":
                return node
        raise AssertionError("No `if settings.PIT_CAPTURE_ENABLED:` block found")

    def test_populate_paper_features_runs_before_run_pre_compute(self):
        """The real serve-time consumer (signals/lgbm_ranker.py's pre_compute,
        reached via global_registry.run_pre_compute) must see the paper_*
        columns -- so the populate call must precede it in source order."""
        func_node = self._parse_run_body()
        populate_lineno = self._find_call_lineno(func_node, "populate_live_paper_features")
        run_pre_compute_lineno = self._find_call_lineno(func_node, "run_pre_compute")
        assert populate_lineno < run_pre_compute_lineno, (
            "populate_live_paper_features(...) must be called BEFORE "
            "global_registry.run_pre_compute(...) -- otherwise the paper_* "
            "columns don't exist yet when signals/lgbm_ranker.py's "
            "pre_compute reads ctx.dashboard_df."
        )

    def test_populate_paper_features_runs_before_pit_df_snapshot_copy(self):
        """The PIT-snapshot training-panel writer's `pit_df = ctx.dashboard_df.copy()`
        must be taken AFTER the paper_* columns are written, or the persisted
        snapshot never sees them either."""
        func_node = self._parse_run_body()
        populate_lineno = self._find_call_lineno(func_node, "populate_live_paper_features")
        pit_df_copy_lineno = self._find_assign_lineno(func_node, "pit_df")
        assert populate_lineno < pit_df_copy_lineno, (
            "populate_live_paper_features(...) must be called BEFORE "
            "`pit_df = ctx.dashboard_df.copy()` -- otherwise the PIT "
            "snapshot writer copies a stale frame missing the paper_* columns."
        )

    def test_populate_paper_features_not_gated_behind_pit_capture_enabled(self):
        """settings.PIT_CAPTURE_ENABLED only controls whether today's PIT
        snapshot is written to disk for future retrains (see its own
        settings.py field description) -- it has nothing to do with whether
        THIS cycle's live inference sees the paper_* features, so the
        populate call must sit entirely outside that `if` block."""
        func_node = self._parse_run_body()
        populate_lineno = self._find_call_lineno(func_node, "populate_live_paper_features")
        if_node = self._find_pit_capture_if(func_node)
        inside_if_block = if_node.lineno <= populate_lineno <= (if_node.end_lineno or if_node.lineno)
        assert not inside_if_block, (
            "populate_live_paper_features(...) must NOT be nested inside "
            "`if settings.PIT_CAPTURE_ENABLED:` -- that flag is unrelated "
            "to live-inference feature population."
        )
        # And it must textually precede the if-block's own opening line, not
        # merely be a sibling statement located later in the function body.
        assert populate_lineno < if_node.lineno


# ---------------------------------------------------------------------------
# Data-driven proof: populate_live_paper_features actually writes real values
# ---------------------------------------------------------------------------
class TestPopulateLivePaperFeaturesDataDriven:
    def _paper_orders_fixture(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"client_order_id": "1", "symbol": "AAPL", "side": "buy", "qty": 10,
             "target_qty": 10, "filled_qty": 10, "timestamp": pd.Timestamp("2023-04-16")},
            {"client_order_id": "2", "symbol": "AAPL", "side": "sell", "qty": 20,
             "target_qty": 20, "filled_qty": 15, "timestamp": pd.Timestamp("2023-04-26")},
            {"client_order_id": "3", "symbol": "MSFT", "side": "buy", "qty": 5,
             "target_qty": 5, "filled_qty": 5, "timestamp": pd.Timestamp("2023-04-20")},
        ])

    def _paper_closed_trades_fixture(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"symbol": "AAPL", "exit_ts": pd.Timestamp("2023-04-27"), "realized_pnl": 150.0},
            {"symbol": "AAPL", "exit_ts": pd.Timestamp("2023-04-28"), "realized_pnl": -50.0},
            {"symbol": "MSFT", "exit_ts": pd.Timestamp("2023-04-21"), "realized_pnl": 40.0},
        ])

    def test_populate_writes_non_nan_paper_columns_for_symbols_with_history(self, monkeypatch):
        paper_orders = self._paper_orders_fixture()
        paper_closed_trades = self._paper_closed_trades_fixture()
        monkeypatch.setattr("ml.training_data._load_all_paper_orders", lambda: paper_orders)
        monkeypatch.setattr(
            "ml.training_data._load_all_paper_closed_trades", lambda: paper_closed_trades
        )

        dashboard_df = pd.DataFrame({"Symbol": ["AAPL", "MSFT", "GOOG"]})
        as_of_date = pd.Timestamp("2023-05-01")

        populate_live_paper_features(dashboard_df, as_of_date)

        paper_cols = [
            "paper_order_count_30d", "paper_size_variance_30d",
            "paper_size_vs_kelly_ratio_30d", "paper_hit_rate_30d",
            "paper_avg_realized_pnl_30d", "paper_fill_rate_30d",
            "paper_has_history_30d",
        ]
        for col in paper_cols:
            assert col in dashboard_df.columns

        aapl_row = dashboard_df.loc[dashboard_df["Symbol"] == "AAPL"].iloc[0]
        assert aapl_row["paper_has_history_30d"] == 1.0
        assert aapl_row["paper_order_count_30d"] == 2.0
        assert not np.isnan(aapl_row["paper_fill_rate_30d"])
        assert not np.isnan(aapl_row["paper_hit_rate_30d"])
        assert not np.isnan(aapl_row["paper_avg_realized_pnl_30d"])
        # 1 win (+150), 1 loss (-50) -> hit rate 0.5, mean pnl 50.0
        assert aapl_row["paper_hit_rate_30d"] == pytest.approx(0.5)
        assert aapl_row["paper_avg_realized_pnl_30d"] == pytest.approx(50.0)

        msft_row = dashboard_df.loc[dashboard_df["Symbol"] == "MSFT"].iloc[0]
        assert msft_row["paper_has_history_30d"] == 1.0
        assert msft_row["paper_hit_rate_30d"] == pytest.approx(1.0)

        # GOOG has no paper-order history at all -- must stay honestly NaN
        # (CONSTRAINT #4), never fabricated.
        goog_row = dashboard_df.loc[dashboard_df["Symbol"] == "GOOG"].iloc[0]
        assert goog_row["paper_has_history_30d"] == 0.0
        assert np.isnan(goog_row["paper_order_count_30d"])
        assert np.isnan(goog_row["paper_hit_rate_30d"])

    def test_populate_matches_pit_ticker_row_for_same_symbol_and_date(self, monkeypatch):
        """Both call sites now share _paper_features_for_symbol -- prove they
        actually agree on a real fixture, not just by code inspection."""
        paper_orders = self._paper_orders_fixture()
        paper_closed_trades = self._paper_closed_trades_fixture()
        as_of_date = pd.Timestamp("2023-05-01")

        # populate_live_paper_features path
        dashboard_df = pd.DataFrame({"Symbol": ["AAPL"]})
        monkeypatch.setattr("ml.training_data._load_all_paper_orders", lambda: paper_orders)
        monkeypatch.setattr(
            "ml.training_data._load_all_paper_closed_trades", lambda: paper_closed_trades
        )
        populate_live_paper_features(dashboard_df, as_of_date)
        populate_row = dashboard_df.iloc[0].to_dict()

        # _pit_ticker_row path (independent call, same inputs)
        dates = pd.date_range("2023-01-01", periods=100, freq="B")
        close = pd.Series(np.linspace(100, 110, 100), index=dates)
        pit_row = _pit_ticker_row(close, "AAPL", as_of_date, paper_orders, paper_closed_trades)

        paper_cols = [
            "paper_order_count_30d", "paper_size_variance_30d",
            "paper_size_vs_kelly_ratio_30d", "paper_hit_rate_30d",
            "paper_avg_realized_pnl_30d", "paper_fill_rate_30d",
            "paper_has_history_30d",
        ]
        for col in paper_cols:
            a = populate_row[col]
            b = pit_row[col]
            if isinstance(a, float) and np.isnan(a):
                assert isinstance(b, float) and np.isnan(b), f"{col} mismatch: {a!r} vs {b!r}"
            else:
                assert a == pytest.approx(b), f"{col} mismatch: {a!r} vs {b!r}"

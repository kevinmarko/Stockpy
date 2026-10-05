"""
tests/test_strategy_eval_step_score_column.py
=============================================
The daemon path must carry StrategyEngine's own "Score" onto dashboard_df
(pipeline/production_steps.py::_apply_strategy_score_column). It used to be
NaN-filled for every row — docs/known_issues/daemon_strategy_score_always_nan.md.

Targets the module-level helper directly plus an AST guard on
StrategyEvalStep.run(), which is too heavy to run end to end here (same
convention as tests/test_production_steps_portfolio_gross_cap.py).
"""
from __future__ import annotations

import ast
import inspect
import math
import textwrap

import pandas as pd

import pipeline.production_steps as ps_mod
from pipeline.production_steps import _apply_strategy_score_column


def _df(*symbols):
    return pd.DataFrame({"Symbol": list(symbols)})


class TestApplyStrategyScoreColumn:
    def test_evaluated_scores_round_trip(self):
        df = _df("AAPL", "MSFT")
        _apply_strategy_score_column(df, {"AAPL": {"Score": 82}, "MSFT": {"Score": 34}})
        assert df["Score"].tolist() == [82.0, 34.0]

    def test_unevaluated_symbol_is_nan_not_zero(self):
        df = _df("AAPL", "DEAD")
        _apply_strategy_score_column(df, {"AAPL": {"Score": 60}})
        assert df.loc[0, "Score"] == 60.0
        assert math.isnan(df.loc[1, "Score"])

    def test_none_score_is_nan(self):
        df = _df("AAPL")
        _apply_strategy_score_column(df, {"AAPL": {"Score": None}})
        assert math.isnan(df.loc[0, "Score"])

    def test_genuine_zero_survives(self):
        df = _df("AAPL")
        _apply_strategy_score_column(df, {"AAPL": {"Score": 0}})
        assert df.loc[0, "Score"] == 0.0

    def test_empty_dashboard(self):
        df = _df()
        _apply_strategy_score_column(df, {})
        assert "Score" in df.columns and df.empty

    def test_column_is_float(self):
        df = _df("AAPL", "DEAD")
        _apply_strategy_score_column(df, {"AAPL": {"Score": 70}})
        assert df["Score"].dtype == float


class TestRunWiring:
    def _run_tree(self):
        return ast.parse(textwrap.dedent(inspect.getsource(ps_mod.StrategyEvalStep.run)))

    def test_run_calls_the_helper(self):
        calls = [
            n for n in ast.walk(self._run_tree())
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_apply_strategy_score_column"
        ]
        assert calls, "StrategyEvalStep.run() must call _apply_strategy_score_column"

    def test_run_no_longer_nan_fills_score(self):
        src = inspect.getsource(ps_mod.StrategyEvalStep.run)
        # The only NaN fill left is the except branch of the helper call.
        assert src.count("dashboard_df['Score'] = float('nan')") == 1
        assert "except Exception as score_exc" in src

    def test_eval_results_carry_score(self):
        src = inspect.getsource(ps_mod.StrategyEvalStep.run)
        assert "'Score': strategy_output.get('Score')" in src

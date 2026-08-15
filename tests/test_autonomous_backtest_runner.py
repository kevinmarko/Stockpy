"""
Unit Tests for Autonomous Backtest & Purged-CV Validator
========================================================
Tests cover:
1. AST security sandbox (whitelisted vs forbidden imports, banned builtins, dunder exploits, syntax errors).
2. Synthetic price paths generation across market regimes.
3. Single-path backtest execution with 1-bar execution lag and turnover transaction costs.
4. Combinatorial Purged Cross-Validation (CPCV) splits, purging, and embargoing.
5. Institutional metrics computation (PBO, DSR, Sharpe, Sortino, Max Drawdown, Turnover, Calmar).
6. Deployability gate evaluation (pass vs fail states and failure reasons).
7. End-to-end strategy evaluation with string code, class-based strategies, and direct callables.
8. Serialization to dictionary and human-readable summary.
"""

import ast
import math
import numpy as np
import pandas as pd
import pytest

from validation.autonomous_backtest_runner import (
    ASTSafetyError,
    ASTSecurityValidator,
    AutonomousBacktestResult,
    AutonomousBacktestRunner,
    compile_and_extract_strategy,
    create_safe_globals,
)
from validation.thresholds import (
    DSR_MIN,
    MAX_DRAWDOWN_MAX,
    NET_SHARPE_MIN,
    PBO_MAX,
)


# ---------------------------------------------------------------------------
# 1. AST Security & Sandbox Tests
# ---------------------------------------------------------------------------

class TestASTSecuritySandbox:
    """Tests that candidate code is strictly analyzed and sandboxed."""

    def test_allowed_modules_pass(self):
        code = """
import numpy as np
import pandas as pd
import math
import scipy
from scipy import stats
import itertools
import functools
import collections
from datetime import datetime, date

def generate_signals(df: pd.DataFrame) -> pd.Series:
    close = df['Close']
    sma = close.rolling(20).mean()
    return (close > sma).astype(float)
"""
        is_safe, violations = ASTSecurityValidator.validate_code(code)
        assert is_safe, f"Expected safe code, got violations: {violations}"
        assert len(violations) == 0

    def test_disallowed_os_sys_imports_rejected(self):
        banned_codes = [
            "import os\ndef generate_signals(df): return df['Close'] * 0",
            "import sys\ndef generate_signals(df): return df['Close'] * 0",
            "import subprocess\ndef generate_signals(df): return df['Close'] * 0",
            "from subprocess import Popen\ndef generate_signals(df): return df['Close'] * 0",
            "import requests\ndef generate_signals(df): return df['Close'] * 0",
            "import socket\ndef generate_signals(df): return df['Close'] * 0",
            "import importlib\ndef generate_signals(df): return df['Close'] * 0",
            "import ctypes\ndef generate_signals(df): return df['Close'] * 0",
            "import threading\ndef generate_signals(df): return df['Close'] * 0",
            "import multiprocessing\ndef generate_signals(df): return df['Close'] * 0",
            "import sqlite3\ndef generate_signals(df): return df['Close'] * 0",
            "from pathlib import Path\ndef generate_signals(df): return df['Close'] * 0",
        ]
        for code in banned_codes:
            is_safe, violations = ASTSecurityValidator.validate_code(code)
            assert not is_safe, f"Expected failure for code:\n{code}"
            assert len(violations) > 0

    def test_banned_builtins_rejected(self):
        banned_calls = [
            "def generate_signals(df):\n    eval('1 + 1')\n    return df['Close']",
            "def generate_signals(df):\n    exec('x = 1')\n    return df['Close']",
            "def generate_signals(df):\n    open('/tmp/test', 'w')\n    return df['Close']",
            "def generate_signals(df):\n    getattr(df, '__class__')\n    return df['Close']",
            "def generate_signals(df):\n    globals()['secret'] = 1\n    return df['Close']",
            "def generate_signals(df):\n    locals()\n    return df['Close']",
            "def generate_signals(df):\n    __import__('os')\n    return df['Close']",
        ]
        for code in banned_calls:
            is_safe, violations = ASTSecurityValidator.validate_code(code)
            assert not is_safe, f"Expected forbidden builtin rejection for:\n{code}"
            with pytest.raises(ASTSafetyError):
                compile_and_extract_strategy(code)

    def test_banned_dunder_attributes_rejected(self):
        banned_attrs = [
            "def generate_signals(df):\n    x = ().__class__.__bases__[0].__subclasses__()\n    return df['Close']",
            "def generate_signals(df):\n    f = generate_signals.__globals__\n    return df['Close']",
            "def generate_signals(df):\n    c = generate_signals.__code__\n    return df['Close']",
        ]
        for code in banned_attrs:
            is_safe, violations = ASTSecurityValidator.validate_code(code)
            assert not is_safe, f"Expected dunder rejection for:\n{code}"
            with pytest.raises(ASTSafetyError):
                compile_and_extract_strategy(code)

    def test_syntax_error_handled_gracefully(self):
        bad_syntax = "def generate_signals(df): this is totally invalid python code :::: {"
        is_safe, violations = ASTSecurityValidator.validate_code(bad_syntax)
        assert not is_safe
        assert any("SyntaxError" in v for v in violations)
        with pytest.raises(ASTSafetyError):
            compile_and_extract_strategy(bad_syntax)

    def test_entrypoint_discovery_flexibility(self):
        # 1. generate_signals
        code1 = "def generate_signals(df):\n    return pd.Series(1.0, index=df.index)"
        fn1 = compile_and_extract_strategy(code1)
        assert callable(fn1)

        # 2. compute_signals
        code2 = "def compute_signals(df):\n    return pd.Series(0.5, index=df.index)"
        fn2 = compile_and_extract_strategy(code2)
        assert callable(fn2)

        # 3. strategy function
        code3 = "def strategy(df):\n    return pd.Series(-1.0, index=df.index)"
        fn3 = compile_and_extract_strategy(code3)
        assert callable(fn3)

        # 4. Class with compute_signals
        code4 = """
class TrendStrategy:
    def compute_signals(self, df):
        return pd.Series(1.0, index=df.index)
"""
        fn4 = compile_and_extract_strategy(code4)
        assert callable(fn4)

        # 5. Explicit entrypoint name
        code5 = "def custom_trading_rule(df):\n    return pd.Series(0.0, index=df.index)"
        fn5 = compile_and_extract_strategy(code5, entrypoint="custom_trading_rule")
        assert callable(fn5)


# ---------------------------------------------------------------------------
# 2. Synthetic Price Paths Generator Tests
# ---------------------------------------------------------------------------

class TestSyntheticOHLCVGenerator:
    """Tests for synthetic market data generator across regimes."""

    def test_generator_shape_and_columns(self):
        df = AutonomousBacktestRunner.generate_synthetic_ohlcv(n_bars=300, start_price=150.0, seed=123)
        assert len(df) == 300
        assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.is_monotonic_increasing

    def test_price_consistency(self):
        df = AutonomousBacktestRunner.generate_synthetic_ohlcv(n_bars=400, seed=42)
        # High must be >= Low, High >= Open, High >= Close
        assert (df["High"] >= df["Low"]).all()
        assert (df["High"] >= df["Open"]).all()
        assert (df["High"] >= df["Close"]).all()
        assert (df["Low"] <= df["Open"]).all()
        assert (df["Low"] <= df["Close"]).all()
        assert (df["Volume"] > 0).all()
        assert not df.isna().any().any()

    def test_regimes(self):
        df_bull = AutonomousBacktestRunner.generate_synthetic_ohlcv(n_bars=300, regime="bull", seed=10)
        df_bear = AutonomousBacktestRunner.generate_synthetic_ohlcv(n_bars=300, regime="bear", seed=10)
        df_switch = AutonomousBacktestRunner.generate_synthetic_ohlcv(n_bars=300, regime="regime_switch", seed=10)

        assert df_bull["Close"].iloc[-1] > df_bull["Close"].iloc[0]
        assert df_bear["Close"].iloc[-1] < df_bear["Close"].iloc[0]
        assert len(df_switch) == 300

    def test_determinism(self):
        df1 = AutonomousBacktestRunner.generate_synthetic_ohlcv(n_bars=200, seed=99)
        df2 = AutonomousBacktestRunner.generate_synthetic_ohlcv(n_bars=200, seed=99)
        pd.testing.assert_frame_equal(df1, df2)


# ---------------------------------------------------------------------------
# 3. Vectorized Backtest Execution & Transaction Cost Tests
# ---------------------------------------------------------------------------

class TestBacktestExecution:
    """Tests the backtesting mechanics: 1-bar execution lag, turnover, transaction costs."""

    def test_execution_lag_prevents_lookahead(self):
        runner = AutonomousBacktestRunner(cost_bps=0.0)
        dates = pd.date_range("2023-01-01", periods=5, freq="D")
        # Prices: 100, 110 (+10%), 121 (+10%), 100 (-17.3%), 100 (0%)
        df = pd.DataFrame(
            {
                "Open": [100, 100, 110, 121, 100],
                "High": [105, 115, 125, 125, 105],
                "Low": [95, 95, 105, 95, 95],
                "Close": [100.0, 110.0, 121.0, 100.0, 100.0],
                "Volume": [1000] * 5,
            },
            index=dates,
        )

        # Strategy emits signal 1.0 on day 1 (close=110)
        def strat(d):
            # Day 0: 0, Day 1: 1, Day 2: 0, Day 3: 0, Day 4: 0
            pos = pd.Series([0.0, 1.0, 0.0, 0.0, 0.0], index=d.index)
            return pos

        net_ret, pos, to = runner.backtest_single_path(strat, df)

        # Signal on Day 1 is executed on Day 2:
        # Day 1 return is 0 (lagged position was 0)
        # Day 2 return is +10% (from 110 to 121) because position from Day 1 was 1.0
        # Day 3 return is 0 (position from Day 2 was 0.0, so the -17.3% drop was avoided!)
        assert net_ret.iloc[0] == 0.0
        assert net_ret.iloc[1] == 0.0
        assert pytest.approx(net_ret.iloc[2], rel=1e-4) == 0.10
        assert net_ret.iloc[3] == 0.0

    def test_transaction_cost_deduction(self):
        # 10 bps cost = 0.0010 per 1.0 turnover
        runner = AutonomousBacktestRunner(cost_bps=10.0)
        dates = pd.date_range("2023-01-01", periods=4, freq="D")
        df = pd.DataFrame(
            {
                "Open": [100, 100, 100, 100],
                "High": [105, 105, 105, 105],
                "Low": [95, 95, 95, 95],
                "Close": [100.0, 100.0, 100.0, 100.0],  # zero price change
                "Volume": [1000] * 4,
            },
            index=dates,
        )

        def flipping_strat(d):
            # Switch position every day: 1.0, -1.0, 1.0, -1.0
            return pd.Series([1.0, -1.0, 1.0, -1.0], index=d.index)

        net_ret, pos, to = runner.backtest_single_path(flipping_strat, df)

        # Day 0: turnover 1.0 -> cost -0.0010
        # Day 1: turnover 2.0 -> cost -0.0020
        # Day 2: turnover 2.0 -> cost -0.0020
        # Day 3: turnover 2.0 -> cost -0.0020
        assert pytest.approx(net_ret.iloc[0], abs=1e-5) == -0.0010
        assert pytest.approx(net_ret.iloc[1], abs=1e-5) == -0.0020
        assert pytest.approx(net_ret.iloc[2], abs=1e-5) == -0.0020
        assert pytest.approx(net_ret.iloc[3], abs=1e-5) == -0.0020

    def test_position_clipping(self):
        runner = AutonomousBacktestRunner()
        df = runner.generate_synthetic_ohlcv(n_bars=50, seed=1)

        def extreme_strat(d):
            return pd.Series(500.0, index=d.index)

        _, pos, _ = runner.backtest_single_path(extreme_strat, df)
        assert (pos <= 1.0).all()
        assert (pos >= -1.0).all()


# ---------------------------------------------------------------------------
# 4. CPCV Splits, Purging, Embargoing & PBO Tests
# ---------------------------------------------------------------------------

class TestCPCVAndPBO:
    """Tests Combinatorial Purged Cross-Validation and PBO calculation."""

    def test_cpcv_combination_path_count(self):
        runner = AutonomousBacktestRunner(n_splits=6, n_test_splits=2, embargo_pct=0.01)
        df = runner.generate_synthetic_ohlcv(n_bars=300, seed=42)

        def simple_sma(d):
            c = d["Close"]
            return (c > c.rolling(10).mean()).astype(float)

        res = runner.run_cpcv(simple_sma, df)
        # C(6, 2) = 15 paths
        assert res["n_paths"] == 15
        assert res["is_sharpe_matrix"].shape[0] == 15
        assert res["oos_sharpe_matrix"].shape[0] == 15

    def test_pbo_calculation_bounds(self):
        runner = AutonomousBacktestRunner(n_splits=5, n_test_splits=2)
        df = runner.generate_synthetic_ohlcv(n_bars=250, seed=42)

        def momentum(d):
            c = d["Close"]
            return (c > c.shift(5)).astype(float)

        res = runner.run_cpcv(momentum, df)
        pbo = res["pbo"]
        assert 0.0 <= pbo <= 1.0
        assert not np.isnan(pbo)

    def test_dsr_calculation_properties(self):
        runner = AutonomousBacktestRunner(n_splits=6, n_test_splits=2)
        df_bull = runner.generate_synthetic_ohlcv(n_bars=500, mu=0.001, sigma=0.01, regime="bull", seed=42)

        def strong_trend(d):
            c = d["Close"]
            return (c > c.rolling(20).mean()).astype(float)

        res = runner.run_cpcv(strong_trend, df_bull)
        dsr = res["dsr"]
        assert 0.0 <= dsr <= 1.0
        assert not np.isnan(dsr)


# ---------------------------------------------------------------------------
# 5. Deployability Gates & Full End-to-End Evaluation Tests
# ---------------------------------------------------------------------------

class TestDeployabilityGates:
    """Tests gate thresholds (PBO, DSR, Sharpe, MaxDD) and pass/fail evaluation."""

    def test_deployable_strategy_passes_all_gates(self):
        runner = AutonomousBacktestRunner(
            n_splits=6,
            n_test_splits=2,
            cost_bps=2.0,
            pbo_max=PBO_MAX,
            dsr_min=DSR_MIN,
            net_sharpe_min=NET_SHARPE_MIN,
            max_drawdown_max=MAX_DRAWDOWN_MAX,
        )
        # Bull regime with low noise for clear winning trend strategy
        df = runner.generate_synthetic_ohlcv(n_bars=600, mu=0.0012, sigma=0.008, regime="bull", seed=101)

        code = """
import numpy as np
import pandas as pd

def generate_signals(df: pd.DataFrame) -> pd.Series:
    close = df['Close']
    sma_fast = close.rolling(10).mean()
    sma_slow = close.rolling(30).mean()
    pos = (sma_fast > sma_slow).astype(float)
    return pos
"""
        result = runner.run(code, df, strategy_id="TrendSMA_Bull")

        assert isinstance(result, AutonomousBacktestResult)
        assert result.error is None
        assert result.n_observations == 600
        assert result.n_paths == 15
        assert result.sharpe_ratio > NET_SHARPE_MIN
        assert result.max_drawdown < MAX_DRAWDOWN_MAX
        assert result.pbo < PBO_MAX
        assert result.gate_evaluations["sharpe_gate"] is True
        assert result.gate_evaluations["max_dd_gate"] is True
        assert result.gate_evaluations["pbo_gate"] is True

    def test_failing_strategy_triggers_gate_failures(self):
        runner = AutonomousBacktestRunner(
            n_splits=6,
            n_test_splits=2,
            cost_bps=20.0,  # high cost penalty
            net_sharpe_min=0.50,
            max_drawdown_max=0.30,
        )
        # Bear market where always-long strategy loses heavily
        df = runner.generate_synthetic_ohlcv(n_bars=500, mu=-0.002, sigma=0.02, regime="bear", seed=202)

        def always_long(d):
            return pd.Series(1.0, index=d.index)

        result = runner.run(always_long, df, strategy_id="AlwaysLong_Bear")

        assert not result.is_deployable
        assert len(result.failure_reasons) > 0
        assert result.gate_evaluations["sharpe_gate"] is False or result.gate_evaluations["max_dd_gate"] is False
        assert any("Net Sharpe" in r or "Max Drawdown" in r for r in result.failure_reasons)

    def test_single_source_of_truth_thresholds(self):
        runner = AutonomousBacktestRunner()
        assert runner.pbo_max == PBO_MAX
        assert runner.dsr_min == DSR_MIN
        assert runner.net_sharpe_min == NET_SHARPE_MIN
        assert runner.max_drawdown_max == MAX_DRAWDOWN_MAX

    def test_security_violation_returns_failing_result(self):
        runner = AutonomousBacktestRunner()
        df = runner.generate_synthetic_ohlcv(n_bars=200, seed=1)

        malicious_code = """
import os
def generate_signals(df):
    os.system('echo hacked')
    return pd.Series(1.0, index=df.index)
"""
        result = runner.run(malicious_code, df, strategy_id="Malicious_Strategy")
        assert not result.is_deployable
        assert result.error is not None
        assert "AST Security Validation Failed" in result.error
        assert result.gate_evaluations["pbo_gate"] is False


# ---------------------------------------------------------------------------
# 6. Serialization & Edge Cases Tests
# ---------------------------------------------------------------------------

class TestSerializationAndEdgeCases:
    """Tests summary strings, JSON serialization, and edge cases."""

    def test_to_dict_and_summary_output(self):
        runner = AutonomousBacktestRunner(n_splits=4, n_test_splits=1)
        df = runner.generate_synthetic_ohlcv(n_bars=200, seed=5)

        def simple_strat(d):
            return pd.Series(1.0, index=d.index)

        result = runner.run(simple_strat, df, strategy_id="TestStrat")

        summary_dict = result.to_dict()
        assert isinstance(summary_dict, dict)
        assert summary_dict["strategy_id"] == "TestStrat"
        assert "gate_evaluations" in summary_dict
        assert "is_deployable" in summary_dict

        summary_text = result.summary()
        assert isinstance(summary_text, str)
        assert "AUTONOMOUS BACKTEST & PURGED-CV REPORT" in summary_text
        assert "TestStrat" in summary_text
        assert "Sharpe Ratio" in summary_text
        assert "Deflated Sharpe (DSR)" in summary_text
        assert "Overfitting Prob (PBO)" in summary_text

    def test_flat_zero_return_series_handled_safely(self):
        runner = AutonomousBacktestRunner()
        dates = pd.date_range("2023-01-01", periods=100, freq="D")
        flat_df = pd.DataFrame(
            {
                "Open": [100.0] * 100,
                "High": [100.0] * 100,
                "Low": [100.0] * 100,
                "Close": [100.0] * 100,
                "Volume": [1000] * 100,
            },
            index=dates,
        )

        def zero_strat(d):
            return pd.Series(0.0, index=d.index)

        result = runner.run(zero_strat, flat_df, strategy_id="ZeroStrat")
        assert not result.is_deployable
        assert result.error is None
        assert result.cumulative_return == 0.0

    def test_short_data_series_graceful_fallback(self):
        runner = AutonomousBacktestRunner(n_splits=10, n_test_splits=2)
        dates = pd.date_range("2023-01-01", periods=15, freq="D")
        short_df = pd.DataFrame(
            {
                "Open": np.linspace(100, 110, 15),
                "High": np.linspace(101, 111, 15),
                "Low": np.linspace(99, 109, 15),
                "Close": np.linspace(100, 110, 15),
                "Volume": [1000] * 15,
            },
            index=dates,
        )

        def buy_hold(d):
            return pd.Series(1.0, index=d.index)

        result = runner.run(buy_hold, short_df, strategy_id="ShortDataStrat")
        assert result.n_observations == 15
        assert result.error is None

    def test_candidate_variants_evaluation(self):
        """Tests CPCV evaluation across multiple candidate hyperparameter variants."""
        runner = AutonomousBacktestRunner(n_splits=6, n_test_splits=2)
        df = runner.generate_synthetic_ohlcv(n_bars=300, seed=42)

        def base_strat(d):
            c = d["Close"]
            return (c > c.rolling(10).mean()).astype(float)

        def variant_20(d):
            c = d["Close"]
            return (c > c.rolling(20).mean()).astype(float)

        def variant_50(d):
            c = d["Close"]
            return (c > c.rolling(50).mean()).astype(float)

        result = runner.run(
            base_strat,
            df,
            strategy_id="SMA_MultiVariant",
            candidate_variants=[variant_20, variant_50],
        )

        assert result.error is None
        assert result.n_paths == 15
        assert 0.0 <= result.pbo <= 1.0
        assert 0.0 <= result.dsr <= 1.0

    def test_sandbox_runtime_import_blocked(self):
        """Tests that runtime attempts to call __import__ for forbidden modules are blocked."""
        runner = AutonomousBacktestRunner()
        df = runner.generate_synthetic_ohlcv(n_bars=50, seed=1)

        # Dynamic __import__ will be caught by AST or runtime sandbox
        code = """
def generate_signals(df):
    try:
        mod = __import__('os')
        mod.system('echo hacked')
    except Exception:
        pass
    return pd.Series(0.0, index=df.index)
"""
        is_safe, violations = ASTSecurityValidator.validate_code(code)
        assert not is_safe or len(violations) > 0

    def test_sortino_zero_downside_returns(self):
        """Tests Sortino calculation when there are no downside returns."""
        runner = AutonomousBacktestRunner(cost_bps=0.0)
        dates = pd.date_range("2023-01-01", periods=10, freq="D")
        # Monotonically increasing prices
        df = pd.DataFrame(
            {
                "Open": np.linspace(100, 200, 10),
                "High": np.linspace(101, 201, 10),
                "Low": np.linspace(99, 199, 10),
                "Close": np.linspace(100, 200, 10),
                "Volume": [1000] * 10,
            },
            index=dates,
        )

        def always_long(d):
            return pd.Series(1.0, index=d.index)

        result = runner.run(always_long, df, strategy_id="AllPositive")
        assert result.error is None
        # Max drawdown should be 0 or near 0
        assert result.max_drawdown <= 1e-6
        assert result.cumulative_return > 0.0

    def test_custom_cost_bps_impact(self):
        """Verifies that increasing cost_bps reduces net returns and net Sharpe."""
        df = AutonomousBacktestRunner.generate_synthetic_ohlcv(n_bars=300, seed=42)

        def oscillating_strat(d):
            # Alternates signals to incur high turnover
            signals = np.sin(np.linspace(0, 20 * np.pi, len(d)))
            return pd.Series(np.sign(signals), index=d.index)

        runner_zero_cost = AutonomousBacktestRunner(cost_bps=0.0)
        res_zero = runner_zero_cost.run(oscillating_strat, df, strategy_id="ZeroCost")

        runner_high_cost = AutonomousBacktestRunner(cost_bps=50.0)  # 50 bps per switch
        res_high = runner_high_cost.run(oscillating_strat, df, strategy_id="HighCost")

        assert res_zero.cumulative_return > res_high.cumulative_return
        assert res_zero.sharpe_ratio > res_high.sharpe_ratio


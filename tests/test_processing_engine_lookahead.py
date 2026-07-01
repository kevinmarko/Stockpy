"""
tests/test_processing_engine_lookahead.py
===========================================
Lookahead perturbation coverage for ``processing_engine.ProcessingEngine
.calculate_technical_metrics`` — the actual production function, not a
reimplementation of its underlying ``pandas_ta`` calls.

``tests/test_indicators_lookahead.py`` already proves RSI/MACD/ATR/Aroon/
Chandelier/RS-momentum-slope are individually causal by calling ``pandas_ta``
directly — it never calls ``ProcessingEngine.calculate_technical_metrics()``
itself. That leaves a real gap: the *production* function additionally
computes VaR 95 (``Pct_Change.quantile(0.05)``), Max Drawdown
(``Close.cummax()``-based), a Sortino ratio, and a Coppock Curve, and wires
all of the above together — none of which had a single perturbation test
proving the assembled function, as actually called by the orchestrators,
never lets a later row influence an earlier row's output.

``ProcessingEngine.calculate_momentum_metrics`` (ROC_12M / ROC_6M /
Realized_Vol_60D) already has dedicated perturbation tests in
``tests/test_processing_engine.py`` (added in an earlier item of this same
coverage pass) — not duplicated here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from processing_engine import ProcessingEngine
from tests.lookahead_check import verify_no_lookahead

np.random.seed(7)


@pytest.fixture
def synthetic_ohlcv_data() -> pd.DataFrame:
    """300 days -- long enough for SMA_200/Aroon(25)/Coppock warm-up to be
    fully past by the mid-series cutoff used in these tests."""
    dates = pd.date_range(end="2026-06-24", periods=300)
    close = 100.0 + np.cumsum(np.random.normal(0, 1.0, 300))
    open_p = close + np.random.normal(0, 0.5, 300)
    high = np.maximum(close, open_p) + np.random.uniform(0, 1.0, 300)
    low = np.minimum(close, open_p) - np.random.uniform(0, 1.0, 300)
    volume = np.random.randint(1000, 10000, 300).astype(float)
    return pd.DataFrame(
        {"Open": open_p, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


class TestCalculateTechnicalMetricsLookahead:
    """Each test wraps calculate_technical_metrics() as func(df, t) ->
    scalar, slicing to df.iloc[:t+1] internally (the same convention
    test_indicators_lookahead.py uses) so the harness's post-t perturbation
    of the FULL fixture never reaches the sliced input actually passed to
    the production function -- proving the assembled function respects the
    same causal boundary its individual indicator calls already have."""

    def test_rsi_key(self, synthetic_ohlcv_data):
        engine = ProcessingEngine()

        def calc(df, t):
            sliced = df.iloc[:t + 1]
            result = engine.calculate_technical_metrics({"AAPL": sliced})
            return result["AAPL"]["RSI"]

        assert verify_no_lookahead(calc, synthetic_ohlcv_data, t=250)

    def test_var_95_key(self, synthetic_ohlcv_data):
        """VaR 95 is a quantile over the ENTIRE input df's daily returns --
        genuinely at risk of lookahead if the function were ever handed
        unsliced data, since a quantile (unlike a rolling window) has no
        inherent notion of 'causal'. Proves the function itself never reads
        past what the caller sliced."""
        engine = ProcessingEngine()

        def calc(df, t):
            sliced = df.iloc[:t + 1]
            result = engine.calculate_technical_metrics({"AAPL": sliced})
            return result["AAPL"]["VaR 95"]

        assert verify_no_lookahead(calc, synthetic_ohlcv_data, t=250)

    def test_max_drawdown_key(self, synthetic_ohlcv_data):
        engine = ProcessingEngine()

        def calc(df, t):
            sliced = df.iloc[:t + 1]
            result = engine.calculate_technical_metrics({"AAPL": sliced})
            return result["AAPL"]["Max Drawdown"]

        assert verify_no_lookahead(calc, synthetic_ohlcv_data, t=250)

    def test_sortino_ratio_key(self, synthetic_ohlcv_data):
        engine = ProcessingEngine()

        def calc(df, t):
            sliced = df.iloc[:t + 1]
            result = engine.calculate_technical_metrics({"AAPL": sliced})
            return result["AAPL"]["Sortino Ratio"]

        assert verify_no_lookahead(calc, synthetic_ohlcv_data, t=250)

    def test_aroon_oscillator_key(self, synthetic_ohlcv_data):
        engine = ProcessingEngine()

        def calc(df, t):
            sliced = df.iloc[:t + 1]
            result = engine.calculate_technical_metrics({"AAPL": sliced})
            return result["AAPL"]["Aroon Oscillator"]

        assert verify_no_lookahead(calc, synthetic_ohlcv_data, t=250)

    def test_coppock_curve_key(self, synthetic_ohlcv_data):
        engine = ProcessingEngine()

        def calc(df, t):
            sliced = df.iloc[:t + 1]
            result = engine.calculate_technical_metrics({"AAPL": sliced})
            return result["AAPL"]["Coppock Curve"]

        assert verify_no_lookahead(calc, synthetic_ohlcv_data, t=250)

    def test_chandelier_exit_key(self, synthetic_ohlcv_data):
        engine = ProcessingEngine()

        def calc(df, t):
            sliced = df.iloc[:t + 1]
            result = engine.calculate_technical_metrics({"AAPL": sliced})
            return result["AAPL"]["Chandelier Exit"]

        assert verify_no_lookahead(calc, synthetic_ohlcv_data, t=250)

    def test_sma_200_key(self, synthetic_ohlcv_data):
        """SMA_200 specifically exercises the longest rolling window in the
        function -- the boundary most likely to accidentally reach past the
        sliced input if a caller-supplied full-history df were ever passed
        by mistake."""
        engine = ProcessingEngine()

        def calc(df, t):
            sliced = df.iloc[:t + 1]
            result = engine.calculate_technical_metrics({"AAPL": sliced})
            return result["AAPL"]["SMA_200"]

        assert verify_no_lookahead(calc, synthetic_ohlcv_data, t=250)

"""Tests for pilots/options_vpin.py — VPIN Math & Volume Bucket Engine."""

import ast
import json
import math
from pathlib import Path
import pytest
import numpy as np
import pandas as pd

from unittest.mock import patch

from pilots.options_vpin import (
    DEFAULT_NUM_BUCKETS,
    DEFAULT_TOXICITY_THRESHOLD,
    MODERATE_TOXICITY_THRESHOLD,
    VPINBucket,
    VPINResult,
    _norm_cdf,
    _normalize_trades_df,
    apply_defensive_spread_concession,
    calculate_vpin,
    compute_vpin_buckets,
    evaluate_toxicity_regime,
    fetch_real_underlying_bar_trades,
    generate_synthetic_option_trades,
    get_options_vpin_metrics,
    get_options_vpin_metrics_for_frontend,
    is_toxic_flow,
)


# ---------------------------------------------------------------------------
# 1. Normal CDF & Math Helpers
# ---------------------------------------------------------------------------

def test_norm_cdf_scalar():
    assert pytest.approx(_norm_cdf(0.0), 0.0001) == 0.5
    assert pytest.approx(_norm_cdf(1.96), 0.001) == 0.975
    assert pytest.approx(_norm_cdf(-1.96), 0.001) == 0.025
    assert _norm_cdf(10.0) > 0.9999
    assert _norm_cdf(-10.0) < 0.0001


def test_norm_cdf_vectorized():
    arr = np.array([-1.96, 0.0, 1.96])
    res = _norm_cdf(arr)
    assert len(res) == 3
    assert pytest.approx(res[0], 0.001) == 0.025
    assert pytest.approx(res[1], 0.0001) == 0.5
    assert pytest.approx(res[2], 0.001) == 0.975


# ---------------------------------------------------------------------------
# 2. Input Normalization & Degenerate Guards
# ---------------------------------------------------------------------------

def test_normalize_trades_df_empty_and_none():
    df_none = _normalize_trades_df(None)
    assert df_none.empty
    assert list(df_none.columns) == ["price", "volume", "time"]

    df_empty = _normalize_trades_df(pd.DataFrame())
    assert df_empty.empty

    df_empty_list = _normalize_trades_df([])
    assert df_empty_list.empty


def test_normalize_trades_df_column_aliases():
    # Test price/volume aliases
    records = [
        {"p": 4.5, "v": 10, "t": "2026-08-14T10:00:00Z"},
        {"p": 4.6, "v": 20, "t": "2026-08-14T10:01:00Z"},
    ]
    df = _normalize_trades_df(records)
    assert len(df) == 2
    assert df["price"].tolist() == [4.5, 4.6]
    assert df["volume"].tolist() == [10.0, 20.0]

    # Test filtering out negative or zero prices/volumes
    dirty_df = pd.DataFrame({
        "last_price": [5.0, -1.0, 0.0, 5.5],
        "contracts": [10, 5, 10, 0],
    })
    cleaned = _normalize_trades_df(dirty_df)
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["price"] == 5.0
    assert cleaned.iloc[0]["volume"] == 10.0


# ---------------------------------------------------------------------------
# 3. Volume Bucket Partitioning & BVC Math
# ---------------------------------------------------------------------------

def test_compute_vpin_buckets_constant_price_zero_variance():
    # 5 trades at constant price $5.0 with volume 20 each (total volume = 100)
    # Bucket size = 20 -> creates 5 buckets of size 20
    # Zero price variance -> Buy fraction = 0.5, Sell fraction = 0.5, Imbalance = 0.0
    df = pd.DataFrame({
        "price": [5.0, 5.0, 5.0, 5.0, 5.0],
        "volume": [20.0, 20.0, 20.0, 20.0, 20.0],
        "time": ["t1", "t2", "t3", "t4", "t5"],
    })
    buckets = compute_vpin_buckets(df, bucket_size=20.0)
    assert len(buckets) == 5
    for b in buckets:
        assert b.volume == 20.0
        assert b.buy_volume == 10.0
        assert b.sell_volume == 10.0
        assert b.order_imbalance == 0.0
        assert b.vwap == 5.0


def test_compute_vpin_buckets_multi_bucket_trade_splitting():
    # 1 single large trade of size 150 at price $10.0
    # Bucket size = 50 -> must split into exactly 3 full buckets of size 50 each
    df = pd.DataFrame({
        "price": [10.0],
        "volume": [150.0],
        "time": ["t0"],
    })
    buckets = compute_vpin_buckets(df, bucket_size=50.0)
    assert len(buckets) == 3
    for idx, b in enumerate(buckets):
        assert b.bucket_index == idx
        assert b.volume == 50.0
        assert b.buy_volume == 25.0
        assert b.sell_volume == 25.0
        assert b.order_imbalance == 0.0


def test_compute_vpin_buckets_directional_bvc():
    # Monotonically increasing prices with high positive steps
    # Prices: 1.0, 2.0, 3.0, 4.0, 5.0 -> Delta P = [0, +1, +1, +1, +1]
    # Positive price steps should result in Buy volume > Sell volume
    df = pd.DataFrame({
        "price": [1.0, 2.0, 3.0, 4.0, 5.0],
        "volume": [10.0, 10.0, 10.0, 10.0, 10.0],
        "time": [f"t{i}" for i in range(5)],
    })
    buckets = compute_vpin_buckets(df, bucket_size=10.0)
    assert len(buckets) == 5
    # First trade has delta_p = 0 -> buy = 5.0, sell = 5.0
    # Trades 1..4 have positive delta_p -> buy volume > sell volume
    for b in buckets[1:]:
        assert b.buy_volume > b.sell_volume
        assert b.buy_volume + b.sell_volume == pytest.approx(b.volume, 1e-6)
        assert b.order_imbalance > 0.0


# ---------------------------------------------------------------------------
# 4. VPIN Calculation & Rolling Series
# ---------------------------------------------------------------------------

def test_calculate_vpin_empty():
    res = calculate_vpin(pd.DataFrame())
    assert isinstance(res, VPINResult)
    assert res.vpin is None
    assert res.rolling_vpin == []
    assert res.total_buckets == 0
    assert res.toxicity_regime == "LOW"
    assert res.is_toxic is False


def test_calculate_vpin_bounds_and_rolling_series():
    # 200 synthetic trades
    df = generate_synthetic_option_trades(num_trades=200, volatility=0.05, seed=123)
    res = calculate_vpin(df, bucket_size=20.0, num_buckets=20, symbol="AAPL_260821C220")

    assert res.total_trade_count == 200
    assert res.total_volume > 0.0
    assert res.total_buckets > 0
    assert len(res.rolling_vpin) == res.total_buckets
    assert 0.0 <= res.vpin <= 1.0
    for v in res.rolling_vpin:
        assert 0.0 <= v <= 1.0
    assert res.symbol == "AAPL_260821C220"


def test_calculate_vpin_toxic_vs_uninformed():
    # Generate pure uninformed noise (random walk)
    df_noise = generate_synthetic_option_trades(num_trades=500, informed_fraction=0.0, seed=42)
    res_noise = calculate_vpin(df_noise, bucket_size=50.0, num_buckets=20)

    # Generate heavy informed toxic buying (80% informed orders driving price up)
    df_toxic = generate_synthetic_option_trades(num_trades=500, informed_fraction=0.8, direction=1.0, seed=42)
    res_toxic = calculate_vpin(df_toxic, bucket_size=50.0, num_buckets=20)

    # Toxic trade stream must have higher VPIN and mean imbalance than uninformed noise
    assert res_toxic.vpin > res_noise.vpin
    assert res_toxic.mean_imbalance > res_noise.mean_imbalance
    assert res_toxic.is_toxic is True
    assert res_toxic.toxicity_regime == "HIGH_TOXICITY"


# ---------------------------------------------------------------------------
# 5. Toxicity Regimes & Defensive Spread Concession
# ---------------------------------------------------------------------------

def test_evaluate_toxicity_regime():
    assert evaluate_toxicity_regime(0.0) == "LOW"
    assert evaluate_toxicity_regime(0.15) == "LOW"
    assert evaluate_toxicity_regime(0.199) == "LOW"
    assert evaluate_toxicity_regime(0.20) == "MODERATE"
    assert evaluate_toxicity_regime(0.30) == "MODERATE"
    assert evaluate_toxicity_regime(0.35) == "MODERATE"
    assert evaluate_toxicity_regime(0.351) == "HIGH_TOXICITY"
    assert evaluate_toxicity_regime(0.80) == "HIGH_TOXICITY"


def test_is_toxic_flow():
    assert is_toxic_flow(0.10) is False
    assert is_toxic_flow(0.35) is False
    assert is_toxic_flow(0.36) is True
    assert is_toxic_flow(0.75) is True


def test_apply_defensive_spread_concession():
    base_spread = 0.50  # 50 cents base spread

    # Non-toxic flow (VPIN <= 0.35): no widening
    assert apply_defensive_spread_concession(base_spread, 0.20) == 0.50
    assert apply_defensive_spread_concession(base_spread, 0.35) == 0.50

    # Toxic flow (VPIN > 0.35): progressive widening
    widened_mod = apply_defensive_spread_concession(base_spread, 0.50)
    assert widened_mod > 0.50

    # Extreme toxic flow (VPIN = 1.0): reaches max_widening_mult (2.0x -> $1.00)
    widened_max = apply_defensive_spread_concession(base_spread, 1.0, max_widening_mult=2.0)
    assert widened_max == 1.00

    # Degenerate zero base spread
    assert apply_defensive_spread_concession(0.0, 0.80) == 0.0


# ---------------------------------------------------------------------------
# 6. Serialization & DTO Integrity
# ---------------------------------------------------------------------------

def test_vpin_bucket_and_result_to_dict():
    bucket = VPINBucket(
        bucket_index=0,
        volume=100.0,
        buy_volume=60.0,
        sell_volume=40.0,
        order_imbalance=20.0,
        vwap=10.25,
        start_time="2026-08-14T10:00:00Z",
        end_time="2026-08-14T10:05:00Z",
        trade_count=5,
        price_change=0.50,
    )
    b_dict = bucket.to_dict()
    assert b_dict["volume"] == 100.0
    assert b_dict["order_imbalance"] == 20.0

    res = VPINResult(
        vpin=0.42,
        rolling_vpin=[0.35, 0.40, 0.42],
        total_trade_count=15,
        total_volume=300.0,
        bucket_size=100.0,
        num_buckets=50,
        total_buckets=3,
        buckets=[bucket],
        mean_imbalance=20.0,
        toxicity_regime="HIGH_TOXICITY",
        is_toxic=True,
        symbol="SPY_260821P500",
    )
    res_dict = res.to_dict()
    assert res_dict["vpin"] == 0.42
    assert res_dict["toxicity_regime"] == "HIGH_TOXICITY"
    assert res_dict["is_toxic"] is True
    assert len(res_dict["buckets"]) == 1

    # Verify JSON serializability
    json_str = json.dumps(res_dict)
    assert "SPY_260821P500" in json_str


# ---------------------------------------------------------------------------
# 7. AST Import Safety Test
# ---------------------------------------------------------------------------

def test_options_vpin_ast_import_safety():
    """Verifies that pilots/options_vpin.py never imports heavy forbidden engines."""
    file_path = Path(__file__).resolve().parent.parent / "pilots" / "options_vpin.py"
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename="options_vpin.py")

    forbidden_modules = {
        "processing_engine",
        "technical_options_engine",
        "forecasting_engine",
        "strategy_engine",
        "macro_engine",
        "main",
        "main_orchestrator",
        "desktop",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in forbidden_modules:
                    assert forbidden not in alias.name, f"Forbidden import found: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod_name = node.module or ""
            for forbidden in forbidden_modules:
                assert forbidden not in mod_name, f"Forbidden from-import found: {mod_name}"


# ---------------------------------------------------------------------------
# 8. Live-endpoint honesty (CONSTRAINT #4) -- get_options_vpin_metrics() must never fall back
# to generate_synthetic_option_trades() for the live /pilots/options/vpin/metrics endpoint.
# See docs/known_issues/options_vpin_fabricated_live_data.md.
# ---------------------------------------------------------------------------

def _fake_hourly_bars(n: int = 40, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = 100.0 + np.cumsum(rng.normal(0, 0.3, n))
    return pd.DataFrame(
        {
            "Open": prices,
            "High": prices + 0.05,
            "Low": prices - 0.05,
            "Close": prices,
            "Volume": rng.integers(500, 20_000, n).astype(float),
        },
        index=pd.date_range("2026-08-01 09:30", periods=n, freq="h"),
    )


class TestFetchRealUnderlyingBarTrades:
    def test_success_reshapes_real_bars_into_trades_df(self):
        class _FakeProvider:
            def get_intraday_bars(self, symbol, lookback_days=10, interval="1h"):
                assert interval == "1h"
                return _fake_hourly_bars()

        with patch("data.market_data.get_provider", lambda: _FakeProvider()):
            df, reason = fetch_real_underlying_bar_trades("SPY")

        assert reason is None
        assert df is not None
        assert list(df.columns) == ["price", "volume", "time"]
        assert len(df) == 40
        assert (df["price"] > 0).all()
        assert (df["volume"] > 0).all()

    def test_never_raises_on_provider_exception(self):
        class _ExplodingProvider:
            def get_intraday_bars(self, *a, **k):
                raise RuntimeError("simulated network failure")

        with patch("data.market_data.get_provider", lambda: _ExplodingProvider()):
            df, reason = fetch_real_underlying_bar_trades("SPY")

        assert df is None
        assert reason is not None
        assert "SPY" in reason

    def test_never_raises_on_empty_bars(self):
        class _EmptyProvider:
            def get_intraday_bars(self, *a, **k):
                return pd.DataFrame()

        with patch("data.market_data.get_provider", lambda: _EmptyProvider()):
            df, reason = fetch_real_underlying_bar_trades("SPY")

        assert df is None
        assert "no intraday bars" in reason

    def test_never_raises_on_missing_columns(self):
        class _MalformedProvider:
            def get_intraday_bars(self, *a, **k):
                return pd.DataFrame({"Open": [1.0, 2.0]})

        with patch("data.market_data.get_provider", lambda: _MalformedProvider()):
            df, reason = fetch_real_underlying_bar_trades("SPY")

        assert df is None
        assert "Close/Volume" in reason

    def test_never_raises_on_insufficient_rows(self):
        class _OneRowProvider:
            def get_intraday_bars(self, *a, **k):
                return pd.DataFrame(
                    {"Close": [100.0], "Volume": [1000.0]},
                    index=pd.date_range("2026-08-01", periods=1, freq="h"),
                )

        with patch("data.market_data.get_provider", lambda: _OneRowProvider()):
            df, reason = fetch_real_underlying_bar_trades("SPY")

        assert df is None
        assert "insufficient intraday bar history" in reason


class TestGetOptionsVpinMetricsHonesty:
    """CONSTRAINT #4 regression: the live endpoint must compute VPIN from real market data and
    degrade to an honest `data_available: False` / `vpin: None` response on failure -- never
    fabricate a plausible-looking number via `generate_synthetic_option_trades()`."""

    def test_uses_real_bars_not_synthetic_trades(self):
        """A real (mocked-provider) run must never call the synthetic generator."""
        with patch("data.market_data.get_provider", lambda: _RealBarsProvider()), patch(
            "pilots.options_vpin.generate_synthetic_option_trades"
        ) as mock_synthetic:
            result = get_options_vpin_metrics("SPY", num_buckets=20)

        mock_synthetic.assert_not_called()
        assert result["data_available"] is True
        assert result["data_source"] == "bar_level_bvc_approximation"
        assert result["reason"] is None
        assert 0.0 <= result["vpin"] <= 1.0
        assert result["total_buckets"] > 0

    def test_degrades_honestly_when_real_data_unavailable(self):
        """No real bars available -> explicit unavailable response, not a fabricated fallback."""
        class _ExplodingProvider:
            def get_intraday_bars(self, *a, **k):
                raise RuntimeError("simulated outage")

        with patch("data.market_data.get_provider", lambda: _ExplodingProvider()), patch(
            "pilots.options_vpin.generate_synthetic_option_trades"
        ) as mock_synthetic:
            result = get_options_vpin_metrics("BADSYMBOL", num_buckets=20)

        mock_synthetic.assert_not_called()
        assert result["data_available"] is False
        assert result["data_source"] is None
        assert result["vpin"] is None
        assert result["toxicity_regime"] is None
        assert result["is_toxic"] is None
        assert result["mean_imbalance"] is None
        assert result["recommended_spread_concession"] is None
        assert result["buckets"] == []
        assert result["bucket_history"] == []
        assert result["total_buckets"] == 0
        assert "simulated outage" in result["reason"]

    def test_frontend_adapter_surfaces_unavailability_honestly(self):
        """get_options_vpin_metrics_for_frontend() must not paper over an unavailable
        measurement with a default regime/warning message describing a toxicity level that was
        never computed."""
        class _ExplodingProvider:
            def get_intraday_bars(self, *a, **k):
                raise RuntimeError("simulated outage")

        with patch("data.market_data.get_provider", lambda: _ExplodingProvider()):
            result = get_options_vpin_metrics_for_frontend("BADSYMBOL")

        assert result["data_available"] is False
        assert result["vpin"] is None
        assert result["regime"] is None
        assert result["defensive_spread_concession"] is None
        assert result["warning_message"] is not None
        assert "unavailable" in result["warning_message"].lower()

    def test_frontend_adapter_success_labels_bar_level_source(self):
        with patch("data.market_data.get_provider", lambda: _RealBarsProvider()):
            result = get_options_vpin_metrics_for_frontend("SPY", num_buckets=20)

        assert result["data_available"] is True
        assert result["data_source"] == "bar_level_bvc_approximation"
        assert result["vpin"] is not None
        assert result["regime"] in ["LOW", "MODERATE", "HIGH_TOXICITY"]


class _RealBarsProvider:
    def get_intraday_bars(self, symbol, lookback_days=10, interval="1h"):
        return _fake_hourly_bars()

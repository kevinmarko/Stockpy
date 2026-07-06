"""
tests/test_training_panel.py
============================
Fully-offline tests for ml.training_data.build_training_panel — the PIT
training-panel foundation for the Stage-4 ML layer.

Covered:
* Panel shape + (date, ticker) MultiIndex on X/y/t1, wide price_history.
* NO-LOOKAHEAD proof — the feature row at date D is byte-identical whether or
  not bars strictly AFTER D are perturbed to extreme values (mirrors the pattern
  in tests/test_triple_barrier_lookahead.py).
* Empty-universe → correctly-shaped empty frames (no crash).
* PITFeatureStore cache round-trip: after a build, available_dates() is non-empty.

All price data is injected via a deterministic offline data engine — no network.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data_engine import IDataProvider
from ml.feature_engineering import FEATURE_COLUMNS
from ml.data.store import PITFeatureStore
from ml.training_data import build_training_panel


# ---------------------------------------------------------------------------
# Deterministic offline data engine with DISTINCT, long-enough per-ticker series
# ---------------------------------------------------------------------------
class _FakeBarsEngine(IDataProvider):
    """Minimal IDataProvider that serves pre-built per-ticker OHLCV frames."""

    def __init__(self, frames: dict[str, pd.DataFrame]):
        self._frames = frames

    def fetch_technical_raw(self, tickers):
        return {t: self._frames[t] for t in tickers if t in self._frames}

    # Unused-by-training-panel abstract surface (kept minimal / inert).
    def fetch_macro_raw(self):
        return {}

    def fetch_macro_history(self):
        return pd.DataFrame()

    def fetch_fundamentals_raw(self, tickers):
        return {t: {} for t in tickers}

    def fetch_options_chain(self, ticker, expiration=None):
        return None


def _make_ohlcv(seed: int, n: int = 400, start: str = "2020-01-01") -> pd.DataFrame:
    """Deterministic geometric-random-walk OHLCV, tz-naive business-day index."""
    rng = np.random.default_rng(seed)
    log_rets = rng.normal(0.0004, 0.012, size=n)
    close = 100.0 * np.exp(np.cumsum(log_rets))
    dates = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": np.full(n, 1_000_000),
        },
        index=dates,
    )


@pytest.fixture
def engine():
    frames = {
        "AAA": _make_ohlcv(seed=1),
        "BBB": _make_ohlcv(seed=2),
        "CCC": _make_ohlcv(seed=3),
    }
    return _FakeBarsEngine(frames)


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    """Redirect PITFeatureStore's default cache dir to a tmp path.

    build_training_panel constructs PITFeatureStore() with no args, so we
    monkeypatch the module-level default cache dir it reads.
    """
    cache_dir = tmp_path / "pit_cache"
    monkeypatch.setattr("ml.data.store._CACHE_DIR", cache_dir, raising=True)
    return cache_dir


# ---------------------------------------------------------------------------
# Test 1: Panel shape + MultiIndex
# ---------------------------------------------------------------------------
def test_panel_shape_and_multiindex(engine, tmp_store):
    universe = ["AAA", "BBB", "CCC"]
    X, y, t1, price_history = build_training_panel(
        "2021-06-01", "2021-06-30", universe, data_engine=engine, horizon_days=21
    )

    assert not X.empty, "expected a non-empty panel"
    # X columns == canonical feature order
    assert list(X.columns) == list(FEATURE_COLUMNS)

    # (date, ticker) MultiIndex on X, y, t1
    for obj in (X, y, t1):
        assert isinstance(obj.index, pd.MultiIndex)
        assert obj.index.names == ["date", "ticker"]

    # y / t1 aligned exactly to X
    assert y.index.equals(X.index)
    assert t1.index.equals(X.index)

    # price_history is wide: columns == tickers, DatetimeIndex
    assert set(price_history.columns) == set(universe)
    assert isinstance(price_history.index, pd.DatetimeIndex)

    # Price-derivable features actually populated (ROC_12M there for enough history)
    assert X["ROC_12M"].notna().any()
    assert X["RSI"].notna().any()

    # t1 (forward-window end) strictly after the event date where computable
    for (dt, _tkr), t1_val in t1.dropna().items():
        assert t1_val > dt


# ---------------------------------------------------------------------------
# Test 2: NO-LOOKAHEAD — perturbing bars strictly after D leaves D's row intact
# ---------------------------------------------------------------------------
def test_no_lookahead_future_perturbation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ml.data.store._CACHE_DIR", tmp_path / "pit_cache_ref", raising=True
    )

    base = {"AAA": _make_ohlcv(seed=7), "BBB": _make_ohlcv(seed=8)}
    engine_ref = _FakeBarsEngine({k: v.copy() for k, v in base.items()})

    cutoff = pd.Timestamp("2021-03-15")
    universe = ["AAA", "BBB"]

    X_ref, _, _, _ = build_training_panel(
        cutoff, cutoff, universe, data_engine=engine_ref, horizon_days=21
    )

    # Perturb every bar STRICTLY AFTER the cutoff to extreme values.
    perturbed = {}
    for tkr, df in base.items():
        pdf = df.copy()
        mask = pdf.index > cutoff
        pdf.loc[mask, ["Open", "High", "Low", "Close"]] *= 10_000.0
        perturbed[tkr] = pdf
    engine_pert = _FakeBarsEngine(perturbed)

    monkeypatch.setattr(
        "ml.data.store._CACHE_DIR", tmp_path / "pit_cache_pert", raising=True
    )
    X_pert, _, _, _ = build_training_panel(
        cutoff, cutoff, universe, data_engine=engine_pert, horizon_days=21
    )

    assert not X_ref.empty and not X_pert.empty
    assert X_ref.index.equals(X_pert.index)

    # Every feature at the cutoff date is unchanged by future perturbation.
    pd.testing.assert_frame_equal(
        X_ref.sort_index(), X_pert.sort_index(), check_exact=False, atol=1e-12
    )


# ---------------------------------------------------------------------------
# Test 3: Empty universe → empty, correctly-shaped frames, no crash
# ---------------------------------------------------------------------------
def test_empty_universe(engine, tmp_store):
    X, y, t1, price_history = build_training_panel(
        "2021-01-01", "2021-12-31", [], data_engine=engine
    )
    assert X.empty and y.empty and t1.empty
    assert list(X.columns) == list(FEATURE_COLUMNS)
    assert isinstance(X.index, pd.MultiIndex)
    assert X.index.names == ["date", "ticker"]
    assert price_history.empty


def test_universe_with_no_bars(tmp_store):
    """A universe whose tickers all yield no bars → empty panel, no crash."""
    engine = _FakeBarsEngine({})  # serves nothing
    X, y, t1, price_history = build_training_panel(
        "2021-01-01", "2021-12-31", ["ZZZ"], data_engine=engine
    )
    assert X.empty and y.empty and t1.empty
    assert price_history.empty


# ---------------------------------------------------------------------------
# Test 4: PITFeatureStore cache round-trip — available_dates() non-empty
# ---------------------------------------------------------------------------
def test_pit_cache_populated(engine, tmp_store):
    universe = ["AAA", "BBB", "CCC"]
    build_training_panel(
        "2021-06-01", "2021-06-15", universe, data_engine=engine, horizon_days=21
    )
    store = PITFeatureStore()  # reads the monkeypatched _CACHE_DIR
    dates = store.available_dates()
    assert len(dates) > 0, "expected PIT snapshots persisted to the cache"

    # Round-trip a snapshot back out and confirm feature-column schema.
    panel = store.read_range("2021-06-01", "2021-06-15")
    assert not panel.empty
    for col in FEATURE_COLUMNS:
        assert col in panel.columns


# ---------------------------------------------------------------------------
# Test 5: One bad symbol is dead-lettered; the good ones still build
# ---------------------------------------------------------------------------
def test_bad_symbol_dead_lettered(tmp_store):
    frames = {"AAA": _make_ohlcv(seed=11)}

    class _PartlyBrokenEngine(_FakeBarsEngine):
        def fetch_technical_raw(self, tickers):
            out = {}
            for t in tickers:
                if t == "BAD":
                    raise RuntimeError("simulated provider failure for BAD")
                if t in self._frames:
                    out[t] = self._frames[t]
            return out

    engine = _PartlyBrokenEngine(frames)
    X, y, t1, price_history = build_training_panel(
        "2021-06-01", "2021-06-10", ["AAA", "BAD"], data_engine=engine, horizon_days=21
    )
    # AAA survives; BAD is dropped, no crash.
    assert "AAA" in price_history.columns
    assert "BAD" not in price_history.columns
    assert not X.empty

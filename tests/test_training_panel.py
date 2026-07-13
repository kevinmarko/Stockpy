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
        "2021-06-01", "2021-06-30", universe, data_engine=engine, horizon_days=21,
        historical_store=False,
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
        cutoff, cutoff, universe, data_engine=engine_ref, horizon_days=21,
        historical_store=False,
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
        cutoff, cutoff, universe, data_engine=engine_pert, horizon_days=21,
        historical_store=False,
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
        "2021-01-01", "2021-12-31", [], data_engine=engine, historical_store=False,
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
        "2021-01-01", "2021-12-31", ["ZZZ"], data_engine=engine, historical_store=False,
    )
    assert X.empty and y.empty and t1.empty
    assert price_history.empty


# ---------------------------------------------------------------------------
# Test 4: PITFeatureStore cache round-trip — available_dates() non-empty
# ---------------------------------------------------------------------------
def test_pit_cache_populated(engine, tmp_store):
    universe = ["AAA", "BBB", "CCC"]
    build_training_panel(
        "2021-06-01", "2021-06-15", universe, data_engine=engine, horizon_days=21,
        historical_store=False,
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
        "2021-06-01", "2021-06-10", ["AAA", "BAD"], data_engine=engine, horizon_days=21,
        historical_store=False,
    )
    # AAA survives; BAD is dropped, no crash.
    assert "AAA" in price_history.columns
    assert "BAD" not in price_history.columns
    assert not X.empty


# ---------------------------------------------------------------------------
# Test 6 (M3): PIT fundamentals merged in via an injected, tmp-DB-backed
# HistoricalStore -- fundamental/factor-Z columns become real, not NaN.
# ---------------------------------------------------------------------------
def _seed_pit_fundamentals(store, symbol, report_date, *, pe, pb, roe, op_margin, market_cap):
    """Write one PIT fundamentals row via the real Gemini-delivered writer."""
    typed = {
        "pe_ratio": pe, "pb_ratio": pb, "roe": roe, "dividend_yield": float("nan"),
        "market_cap": market_cap, "eps": 1.0, "operating_margin": op_margin,
        "debt_to_equity": float("nan"),
    }
    store.upsert_fundamentals_pit(symbol, typed, {}, report_date=report_date, source="test")


@pytest.fixture
def hist_store(tmp_path):
    from data.historical_store import HistoricalStore

    store = HistoricalStore(db_path=str(tmp_path / "test_pit.db"))
    yield store


def test_pit_fundamentals_merged_when_filing_exists(engine, tmp_store, hist_store):
    """A PIT filing dated before the as-of date makes the fundamental AND
    factor-Z columns real (non-NaN) -- the core M3 outcome.

    Fundamentals deliberately DIFFER per ticker: Z-scoring needs real
    cross-sectional variance (a zero-variance cross-section is correctly
    all-NaN by _zscore_winsorize's own zero-std guard)."""
    seeds = {
        "AAA": dict(pe=15.0, pb=2.0, roe=0.15, op_margin=0.20, market_cap=5e10),
        "BBB": dict(pe=22.0, pb=3.5, roe=0.08, op_margin=0.12, market_cap=2e10),
        "CCC": dict(pe=9.0, pb=1.1, roe=0.22, op_margin=0.28, market_cap=8e10),
    }
    for symbol, kwargs in seeds.items():
        _seed_pit_fundamentals(hist_store, symbol, "2021-01-01", **kwargs)

    X, y, t1, _ = build_training_panel(
        "2021-06-01", "2021-06-05", ["AAA", "BBB", "CCC"],
        data_engine=engine, horizon_days=21, historical_store=hist_store,
    )
    assert not X.empty
    assert X["book_to_market"].notna().any()
    assert X["earnings_yield"].notna().any()
    assert X["quality_factor_score"].notna().any()
    # Factor-Z columns populate as a RESULT of the merge (cross-sectional,
    # needs >=2 tickers -- satisfied here with 3).
    assert X["Value_Z"].notna().any()
    assert X["Quality_Z"].notna().any()
    assert X["Size_Z"].notna().any()


def test_no_pit_filing_yet_stays_honest_nan(engine, tmp_store, hist_store):
    """No PIT row exists at all -> fundamentals/factor-Z stay NaN, never
    fabricated -- get_fundamentals_asof's own honest-empty-dict contract."""
    X, y, t1, _ = build_training_panel(
        "2021-06-01", "2021-06-05", ["AAA", "BBB", "CCC"],
        data_engine=engine, horizon_days=21, historical_store=hist_store,
    )
    assert not X.empty
    assert X["book_to_market"].isna().all()
    assert X["Value_Z"].isna().all()


def test_historical_store_false_skips_fundamentals_entirely(engine, tmp_store, hist_store):
    """historical_store=False must behave identically to a store with no
    filings at all -- an explicit, zero-DB-touch opt-out."""
    for symbol in ("AAA", "BBB", "CCC"):
        _seed_pit_fundamentals(
            hist_store, symbol, "2021-01-01",
            pe=15.0, pb=2.0, roe=0.15, op_margin=0.2, market_cap=5e10,
        )
    # Even though hist_store HAS real filings, passing False means they are
    # never even queried.
    X, y, t1, _ = build_training_panel(
        "2021-06-01", "2021-06-05", ["AAA", "BBB", "CCC"],
        data_engine=engine, horizon_days=21, historical_store=False,
    )
    assert not X.empty
    assert X["book_to_market"].isna().all()
    assert X["Value_Z"].isna().all()


def test_pit_fundamentals_no_lookahead(tmp_path, monkeypatch):
    """A filing dated AFTER the as-of date must NOT change that date's row --
    end-to-end proof through build_training_panel, not just of
    get_fundamentals_asof in isolation (mirrors the perturbation style of
    tests/test_lgbm_feature_pit.py / tests/test_triple_barrier_lookahead.py).
    """
    from data.historical_store import HistoricalStore

    cutoff = pd.Timestamp("2021-06-01")
    universe = ["AAA", "BBB", "CCC"]
    frames = {
        "AAA": _make_ohlcv(seed=21),
        "BBB": _make_ohlcv(seed=22),
        "CCC": _make_ohlcv(seed=23),
    }

    monkeypatch.setattr("ml.data.store._CACHE_DIR", tmp_path / "pit_cache_ref", raising=True)
    store_ref = HistoricalStore(db_path=str(tmp_path / "ref.db"))
    for symbol in universe:
        _seed_pit_fundamentals(
            store_ref, symbol, "2021-01-01",
            pe=15.0, pb=2.0, roe=0.15, op_margin=0.2, market_cap=5e10,
        )
    X_ref, _, _, _ = build_training_panel(
        cutoff, cutoff, universe,
        data_engine=_FakeBarsEngine({k: v.copy() for k, v in frames.items()}),
        horizon_days=21, historical_store=store_ref,
    )

    monkeypatch.setattr("ml.data.store._CACHE_DIR", tmp_path / "pit_cache_pert", raising=True)
    store_pert = HistoricalStore(db_path=str(tmp_path / "pert.db"))
    for symbol in universe:
        _seed_pit_fundamentals(
            store_pert, symbol, "2021-01-01",
            pe=15.0, pb=2.0, roe=0.15, op_margin=0.2, market_cap=5e10,
        )
        # A filing dated AFTER the cutoff, with WILDLY different values --
        # if this leaked, book_to_market/earnings_yield would change.
        _seed_pit_fundamentals(
            store_pert, symbol, "2021-06-15",
            pe=1.0, pb=0.01, roe=99.0, op_margin=99.0, market_cap=1.0,
        )
    X_pert, _, _, _ = build_training_panel(
        cutoff, cutoff, universe,
        data_engine=_FakeBarsEngine({k: v.copy() for k, v in frames.items()}),
        horizon_days=21, historical_store=store_pert,
    )

    assert not X_ref.empty and not X_pert.empty
    pd.testing.assert_frame_equal(
        X_ref.sort_index(), X_pert.sort_index(), check_exact=False, atol=1e-12
    )

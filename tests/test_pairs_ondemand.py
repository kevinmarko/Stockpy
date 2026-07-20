"""
tests/test_pairs_ondemand.py
=============================
Offline unit tests for ``pairs_ondemand.py`` — the on-demand (operator-
triggered, synchronous) pairs-trading compute backing
``POST /data/pairs/analyze`` and ``POST /data/pairs/scan`` (webapp porting
backlog item 8a).

No network/provider is real here: a ``_FakeProvider`` serves deterministic
synthetic Close series (a cointegrated pair + an independent random walk,
mirroring ``tests/test_engle_granger.py``'s generator) so cointegration/
signal-generation actually exercise the real ``pairs.cointegration`` /
``signals.pairs_trading`` engines, not a mock.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import pairs_ondemand


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _cointegrated_frame(n: int = 252, seed: int = 42) -> pd.DataFrame:
    """A random-walk X, a cointegrated Y (spread AR(1) coeff 0.9), and an
    independent random-walk Z — mirrors tests/test_engle_granger.py's
    generator, with a real DatetimeIndex so downstream .isoformat() works."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    x = np.cumsum(rng.normal(0, 1, n)) + 100
    spread = [0.0]
    for _ in range(n - 1):
        spread.append(0.9 * spread[-1] + rng.normal(0, 0.5))
    spread = np.array(spread)
    y = 0.5 * x + 10.0 + spread
    z = np.cumsum(rng.normal(0, 1, n)) + 100
    return pd.DataFrame({"Y": y, "X": x, "Z": z}, index=idx)


class _FakeProvider:
    """Serves ``get_intraday_bars`` from a pre-built {symbol: Close} frame."""

    def __init__(self, frame: pd.DataFrame, raise_for: frozenset = frozenset()):
        self._frame = frame
        self._raise_for = raise_for

    def get_intraday_bars(self, symbol: str, lookback_days: int = 252) -> pd.DataFrame:
        if symbol in self._raise_for:
            raise RuntimeError(f"provider outage for {symbol}")
        if symbol not in self._frame.columns:
            return pd.DataFrame()
        close = self._frame[symbol].tail(lookback_days)
        return pd.DataFrame({"Close": close})


# ---------------------------------------------------------------------------
# _finite_or_none / _signal_label / _align_closes — pure helpers
# ---------------------------------------------------------------------------


def test_finite_or_none_handles_nan_inf_and_non_numeric():
    assert pairs_ondemand._finite_or_none(1.5) == 1.5
    assert pairs_ondemand._finite_or_none(float("nan")) is None
    assert pairs_ondemand._finite_or_none(float("inf")) is None
    assert pairs_ondemand._finite_or_none(None) is None
    assert pairs_ondemand._finite_or_none("not a number") is None


def test_signal_label_matches_reporting_pairs_snapshot_wording():
    # Mirrors reporting/pairs_snapshot.py's wording (ENTER LONG/SHORT, "Flat —
    # no entry") -- NOT gui/panels/pairs.py's ("Entry LONG spread ...") --
    # because PairsRadar.tsx's signalColor() branches on this exact wording.
    assert "insufficient history" in pairs_ondemand._signal_label(0.0, float("nan"), 0.05)
    assert "not cointegrated" in pairs_ondemand._signal_label(0.0, 3.0, 0.20)
    assert pairs_ondemand._signal_label(0.0, -2.5, 0.02) == "ENTER LONG spread"
    assert pairs_ondemand._signal_label(0.0, 2.5, 0.02) == "ENTER SHORT spread"
    assert "Flat" in pairs_ondemand._signal_label(0.0, 0.5, 0.02)
    assert "Hold LONG" in pairs_ondemand._signal_label(1.0, -1.5, 0.02)
    assert "Hold SHORT" in pairs_ondemand._signal_label(-1.0, 1.5, 0.02)
    assert "Exit" in pairs_ondemand._signal_label(1.0, 0.1, 0.02)
    assert "STOP" in pairs_ondemand._signal_label(1.0, -4.5, 0.02)
    assert "STOP" in pairs_ondemand._signal_label(-1.0, 4.5, 0.02)


def test_align_closes_inner_join_drops_empty_and_nonoverlap():
    idx_a = pd.date_range("2025-01-01", periods=5, freq="D")
    idx_b = pd.date_range("2025-01-03", periods=5, freq="D")
    series = {
        "AAA": pd.Series(np.arange(5.0), index=idx_a),
        "BBB": pd.Series(np.arange(5.0), index=idx_b),
        "CCC": pd.Series(dtype=float),
        "DDD": None,
    }
    df = pairs_ondemand._align_closes(series)
    assert list(df.columns) == ["AAA", "BBB"]
    assert len(df) == 3  # only the 3 overlapping dates survive


def test_align_closes_fewer_than_two_series_is_empty():
    assert pairs_ondemand._align_closes({"AAA": pd.Series([1.0])}).empty
    assert pairs_ondemand._align_closes({}).empty


# ---------------------------------------------------------------------------
# analyze_pair
# ---------------------------------------------------------------------------


def test_analyze_pair_missing_symbols_is_honest_not_found():
    provider = _FakeProvider(_cointegrated_frame())
    result = pairs_ondemand.analyze_pair("", "X", provider)
    assert result["found"] is False
    assert "required" in result["reason"]


def test_analyze_pair_identical_symbols_is_honest_not_found():
    provider = _FakeProvider(_cointegrated_frame())
    result = pairs_ondemand.analyze_pair("AAPL", "AAPL", provider)
    assert result["found"] is False
    assert "different" in result["reason"]


def test_analyze_pair_success_on_cointegrated_series():
    frame = _cointegrated_frame()
    provider = _FakeProvider(frame)
    result = pairs_ondemand.analyze_pair("Y", "X", provider)
    assert result["found"] is True
    assert result["reason"] is None
    assert result["ticker1"] == "Y"
    assert result["ticker2"] == "X"
    assert isinstance(result["z_score"], float)
    assert isinstance(result["beta"], float)
    assert result["half_life"] is not None
    assert result["half_life_tradeable"] in (True, False)
    assert result["signal"] and result["signal"] != "No signal — insufficient history"
    assert result["aligned_bars"] > 0
    assert isinstance(result["z_score_series"], list) and result["z_score_series"]
    point = result["z_score_series"][0]
    assert set(point) == {"date", "z_score"}
    # Real ISO date strings, not a raw pandas Timestamp repr.
    assert "T" in point["date"] or "-" in point["date"]


def test_analyze_pair_insufficient_history_is_honest_not_found():
    # Only 10 overlapping bars -- well under the 60-bar minimum.
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    frame = pd.DataFrame(
        {"Y": np.linspace(100, 101, 10), "X": np.linspace(50, 50.5, 10)}, index=idx
    )
    provider = _FakeProvider(frame)
    result = pairs_ondemand.analyze_pair("Y", "X", provider)
    assert result["found"] is False
    assert "Insufficient aligned history" in result["reason"]


def test_analyze_pair_provider_failure_is_dead_lettered():
    frame = _cointegrated_frame()
    provider = _FakeProvider(frame, raise_for=frozenset({"Y"}))
    result = pairs_ondemand.analyze_pair("Y", "X", provider)
    assert result["found"] is False
    assert result["reason"] is not None  # never raises


def test_analyze_pair_generate_signals_failure_is_dead_lettered(monkeypatch):
    frame = _cointegrated_frame()
    provider = _FakeProvider(frame)

    import signals.pairs_trading as pairs_trading

    def _boom(y, x, **kwargs):
        raise RuntimeError("kalman filter exploded")

    monkeypatch.setattr(pairs_trading, "generate_pairs_signals", _boom)
    result = pairs_ondemand.analyze_pair("Y", "X", provider)
    assert result["found"] is False
    assert "Could not generate" in result["reason"]


# ---------------------------------------------------------------------------
# scan_pairs
# ---------------------------------------------------------------------------


def test_scan_pairs_finds_the_cointegrated_pair_and_dead_letters_missing():
    frame = _cointegrated_frame()
    provider = _FakeProvider(frame)
    result = pairs_ondemand.scan_pairs(["Y", "X", "Z", "GHOST"], provider, max_pairs=10)
    assert result["reason"] is None
    assert result["missing"] == ["GHOST"]
    pair_tickers = {(r["ticker1"], r["ticker2"]) for r in result["pairs"]}
    # find_cointegrated_pairs iterates price_df.columns in whatever order the
    # aligned frame produced them (alphabetical-ish via `syms = sorted(...)`,
    # not by request order) -- check both orderings rather than assume one.
    assert ("Y", "X") in pair_tickers or ("X", "Y") in pair_tickers
    for row in result["pairs"]:
        assert set(row) == {
            "ticker1", "ticker2", "p_value", "half_life",
            "z_score", "beta", "rolling_p", "position", "signal",
        }


def test_scan_pairs_insufficient_history_is_honest_empty():
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    frame = pd.DataFrame(
        {"A": np.linspace(100, 101, 10), "B": np.linspace(50, 50.5, 10)}, index=idx
    )
    provider = _FakeProvider(frame)
    result = pairs_ondemand.scan_pairs(["A", "B"], provider)
    assert result["pairs"] == []
    assert "Insufficient aligned history" in result["reason"]


def test_scan_pairs_no_cointegration_is_honest_empty_not_error():
    n = 252
    rng = np.random.default_rng(7)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    frame = pd.DataFrame(
        {
            "A": np.cumsum(rng.normal(0, 1, n)) + 100,
            "B": np.cumsum(rng.normal(0, 1, n)) + 200,
        },
        index=idx,
    )
    provider = _FakeProvider(frame)
    result = pairs_ondemand.scan_pairs(["A", "B"], provider)
    assert result["pairs"] == []
    assert result["reason"] is not None
    assert result["missing"] == []


def test_scan_pairs_never_raises_on_cointegration_engine_failure(monkeypatch):
    frame = _cointegrated_frame()
    provider = _FakeProvider(frame)

    import pairs.cointegration as cointegration

    def _boom(df, **kwargs):
        raise RuntimeError("statsmodels exploded")

    monkeypatch.setattr(cointegration, "find_cointegrated_pairs", _boom)
    result = pairs_ondemand.scan_pairs(["Y", "X"], provider)
    assert result["pairs"] == []
    assert "Cointegration scan failed" in result["reason"]


def test_scan_pairs_dedupes_and_uppercases_within_module():
    frame = _cointegrated_frame()
    provider = _FakeProvider(frame)
    # scan_pairs itself upper-cases/dedupes defensively even though the API
    # layer already does this before calling in -- unit-testable independently.
    result = pairs_ondemand.scan_pairs(["y", "Y", "x"], provider, max_pairs=5)
    assert result["aligned_symbols"] == 2

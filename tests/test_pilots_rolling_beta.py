"""
tests/test_pilots_rolling_beta.py
==================================
Tests for ``pilots/rolling_beta.py`` — the rolling beta-vs-SPY reader powering
the mobile SymbolDetail screen's ``GET /symbols/{ticker}/rolling-beta``.

All tests are fully offline: no network calls, no real ``quant_platform.db`` —
every test uses a fresh temporary SQLite database via pytest's ``tmp_path``
fixture, seeded directly through ``HistoricalStore._upsert_bars`` (mirroring
``tests/test_historical_store.py``'s own fixture convention). Price rows are
seeded ending exactly at "today" (the same trick
``TestGetBars::test_up_to_date_skips_provider`` uses) so
``HistoricalStore.get_bars()``'s "no trading days elapsed since the last bar"
defense short-circuits before any live-provider fetch is attempted — the
module never touches the network in these tests.

Covers: happy path (real, non-trivial beta values recovered from a synthetic
series with a KNOWN true beta), insufficient-overlap honest degradation,
missing-ticker / missing-SPY honest degradation, blank-ticker degradation, and
a scoped import-allowlist guard (mirrors ``pilots/attribution.py``'s own
documented "deliberately not stdlib-only" exception).
"""

from __future__ import annotations

import ast
import pathlib
import random

import pandas as pd
import pytest

from data.historical_store import HistoricalStore
from pilots import rolling_beta


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _price_frame(closes, *, end=None) -> pd.DataFrame:
    """Build an OHLCV frame from a list of closes, ending at *end* (default today)."""
    if end is None:
        end = pd.Timestamp.now().normalize()
    n = len(closes)
    dates = pd.bdate_range(end=end, periods=n)
    closes = pd.Series(closes, index=dates)
    return pd.DataFrame(
        {
            "Open": closes.values,
            "High": closes.values * 1.001,
            "Low": closes.values * 0.999,
            "Close": closes.values,
            "Volume": [1_000_000] * n,
        },
        index=dates,
    )


def _correlated_closes(n: int, *, true_beta: float, seed: int = 42):
    """Generate (ticker_closes, spy_closes) where ticker's daily returns are
    ``true_beta * spy_return + small_idiosyncratic_noise`` — a real, recoverable
    linear relationship, not a fabricated flat series."""
    rng = random.Random(seed)
    spy_close = 100.0
    ticker_close = 50.0
    spy_closes = [spy_close]
    ticker_closes = [ticker_close]
    for _ in range(n - 1):
        spy_ret = rng.uniform(-0.02, 0.02)
        noise = rng.uniform(-0.002, 0.002)  # small idiosyncratic term
        ticker_ret = true_beta * spy_ret + noise
        spy_close *= (1.0 + spy_ret)
        ticker_close *= (1.0 + ticker_ret)
        spy_closes.append(spy_close)
        ticker_closes.append(ticker_close)
    return ticker_closes, spy_closes


def _seed_store(db_path: str, symbol: str, closes, *, end=None) -> None:
    """Write-mode HistoricalStore seeding, mirroring test_historical_store.py."""
    store = HistoricalStore(db_path=db_path)
    store._upsert_bars(symbol, _price_frame(closes, end=end), source="yfinance")


def _neutralize_live_provider(monkeypatch) -> None:
    """Force HistoricalStore._resolve_provider(None) to resolve to None.

    HistoricalStore.get_bars() genuinely falls back to a LIVE provider fetch
    when the DB has nothing cached for a symbol (see its own module docstring's
    "Fallback hierarchy") -- a real behavior, not a bug, and this test process
    may have real network access. To deterministically exercise the "truly no
    data available anywhere" honest-degradation path offline (never depending
    on real network conditions), this monkeypatches data.market_data.get_provider
    to raise, so HistoricalStore._resolve_provider's own try/except degrades it
    to None (its own documented dead-letter behavior), and both the DB path and
    the live-fetch fallback converge on an empty DataFrame.
    """
    def _boom():
        raise RuntimeError("network unavailable (test)")

    monkeypatch.setattr("data.market_data.get_provider", _boom)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_recovers_real_beta_values(self, tmp_path):
        db = str(tmp_path / "test.db")
        n = 260
        true_beta = 1.6
        ticker_closes, spy_closes = _correlated_closes(n, true_beta=true_beta)
        today = pd.Timestamp.now().normalize()
        _seed_store(db, "XYZ", ticker_closes, end=today)
        _seed_store(db, "SPY", spy_closes, end=today)

        result = rolling_beta.rolling_beta_view("xyz", window=60, db_path=db)

        assert result["symbol"] == "XYZ"
        assert result["window"] == 60
        assert result["reason"] is None
        assert len(result["series"]) > 0

        # Every entry is a real, finite beta value with an ISO date string.
        betas = [pt["beta"] for pt in result["series"]]
        for pt in result["series"]:
            assert isinstance(pt["date"], str) and len(pt["date"]) == 10
            assert isinstance(pt["beta"], float)

        # The recovered beta should be close to the TRUE beta used to generate
        # the synthetic series (not exact, due to idiosyncratic noise, but well
        # within a wide tolerance) -- proves this is a real computation, not a
        # fabricated passthrough.
        avg_beta = sum(betas) / len(betas)
        assert avg_beta == pytest.approx(true_beta, abs=0.5)

        # Dates are ascending (never fabricated/forward-filled ordering).
        dates = [pt["date"] for pt in result["series"]]
        assert dates == sorted(dates)

    def test_series_length_matches_expected_rolling_window_math(self, tmp_path):
        """With n overlapping rows and window w, the number of valid rolling
        beta points is n - w (the first w-1 pct_change/rolling rows are NaN,
        dropped rather than fabricated)."""
        db = str(tmp_path / "test.db")
        n = 150
        window = 30
        ticker_closes, spy_closes = _correlated_closes(n, true_beta=0.8)
        today = pd.Timestamp.now().normalize()
        _seed_store(db, "ABC", ticker_closes, end=today)
        _seed_store(db, "SPY", spy_closes, end=today)

        result = rolling_beta.rolling_beta_view("ABC", window=window, db_path=db)
        assert result["reason"] is None
        # n rows -> n-1 returns -> rolling(window) needs `window` valid returns,
        # so the first valid beta appears at row index `window` (0-based) of
        # the return series, i.e. n - window points survive.
        assert len(result["series"]) == n - window


# ---------------------------------------------------------------------------
# Honest degradation
# ---------------------------------------------------------------------------


class TestHonestDegradation:
    def test_insufficient_overlap_returns_empty_with_reason(self, tmp_path):
        db = str(tmp_path / "test.db")
        window = 60
        # Only 20 overlapping rows -- well short of the 60-day window.
        ticker_closes, spy_closes = _correlated_closes(20, true_beta=1.0)
        today = pd.Timestamp.now().normalize()
        _seed_store(db, "SHRT", ticker_closes, end=today)
        _seed_store(db, "SPY", spy_closes, end=today)

        result = rolling_beta.rolling_beta_view("SHRT", window=window, db_path=db)
        assert result["series"] == []
        assert result["reason"] is not None
        assert "Not enough overlapping history" in result["reason"]
        assert result["symbol"] == "SHRT"
        assert result["window"] == window

    def test_unknown_ticker_no_bars_returns_empty_with_reason(self, tmp_path, monkeypatch):
        _neutralize_live_provider(monkeypatch)
        db = str(tmp_path / "test.db")
        # Seed SPY only -- the target ticker has no cached bars at all, and the
        # live-provider fallback is neutralized so this stays a genuine
        # "no data anywhere" case rather than depending on real network access.
        _, spy_closes = _correlated_closes(120, true_beta=1.0)
        today = pd.Timestamp.now().normalize()
        _seed_store(db, "SPY", spy_closes, end=today)

        result = rolling_beta.rolling_beta_view("ZZZZ", window=60, db_path=db)
        assert result["series"] == []
        assert result["reason"] is not None
        assert "No cached price history" in result["reason"]

    def test_missing_spy_bars_returns_empty_with_reason(self, tmp_path, monkeypatch):
        _neutralize_live_provider(monkeypatch)
        db = str(tmp_path / "test.db")
        ticker_closes, _ = _correlated_closes(120, true_beta=1.0)
        today = pd.Timestamp.now().normalize()
        _seed_store(db, "AAPL", ticker_closes, end=today)
        # SPY deliberately not seeded, and the live-provider fallback is
        # neutralized above so this stays a genuine "no data anywhere" case.
        result = rolling_beta.rolling_beta_view("AAPL", window=60, db_path=db)
        assert result["series"] == []
        assert "No cached SPY price history" in result["reason"]

    def test_blank_ticker_returns_empty_with_reason(self):
        result = rolling_beta.rolling_beta_view("   ", window=60)
        assert result["series"] == []
        assert result["reason"] == "No ticker supplied."
        assert result["symbol"] == ""

    def test_cold_db_never_raises(self, tmp_path, monkeypatch):
        """A totally fresh, empty DB path (no tables yet) degrades honestly
        rather than raising -- readonly HistoricalStore construction assumes
        the schema exists, so a genuinely nonexistent DB file exercises the
        dead-letter path end-to-end. The live-provider fallback is neutralized
        so this stays a genuine "no data available anywhere" case rather than
        depending on this test process's real network access."""
        _neutralize_live_provider(monkeypatch)
        db = str(tmp_path / "does_not_exist.db")
        result = rolling_beta.rolling_beta_view("AAPL", window=60, db_path=db)
        assert result["series"] == []
        assert result["reason"] is not None
        assert result["symbol"] == "AAPL"


# ---------------------------------------------------------------------------
# Window clamping
# ---------------------------------------------------------------------------


class TestWindowClamping:
    def test_default_window_is_60(self, tmp_path):
        db = str(tmp_path / "test.db")
        result = rolling_beta.rolling_beta_view("AAPL", db_path=db)
        assert result["window"] == 60

    def test_window_clamped_to_max(self, tmp_path):
        db = str(tmp_path / "test.db")
        result = rolling_beta.rolling_beta_view("AAPL", window=9999, db_path=db)
        assert result["window"] == rolling_beta._MAX_WINDOW

    def test_window_clamped_to_min(self, tmp_path):
        db = str(tmp_path / "test.db")
        result = rolling_beta.rolling_beta_view("AAPL", window=0, db_path=db)
        assert result["window"] == rolling_beta._MIN_WINDOW

    def test_non_numeric_window_falls_back_to_default(self, tmp_path):
        db = str(tmp_path / "test.db")
        result = rolling_beta.rolling_beta_view("AAPL", window="garbage", db_path=db)  # type: ignore[arg-type]
        assert result["window"] == rolling_beta._DEFAULT_WINDOW


# ---------------------------------------------------------------------------
# Import scope guard
# ---------------------------------------------------------------------------


_HEAVY_ENGINE_DENYLIST = {
    "processing_engine",
    "strategy_engine",
    "forecasting_engine",
    "macro_engine",
    "technical_options_engine",
    "main_orchestrator",
    "desktop",
    "signals",
}


def test_never_imports_a_heavy_engine():
    """pilots/rolling_beta.py deliberately reimplements calculate_rolling_beta's
    math rather than importing processing_engine (which api/pilots_api.py's AST
    guard forbids -- see tests/test_pilots_api.py::
    test_pilots_api_never_imports_heavy_engines). Like pilots/attribution.py,
    this module is NOT on the ultra-light stdlib-only allowlist (it genuinely
    needs pandas + data.historical_store), so this is a scoped denylist guard
    rather than the stricter stdlib-only allowlist test."""
    path = pathlib.Path(__file__).resolve().parent.parent / "pilots" / "rolling_beta.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    assert not (roots & _HEAVY_ENGINE_DENYLIST), (
        f"pilots/rolling_beta.py imports a forbidden heavy engine: {roots & _HEAVY_ENGINE_DENYLIST}"
    )

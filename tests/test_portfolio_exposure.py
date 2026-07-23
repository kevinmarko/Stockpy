"""
tests/test_portfolio_exposure.py
=================================
Unit tests for ``engine.portfolio_exposure`` (Phase 2 PR3, 3a — Portfolio
net-exposure classifier).

Coverage
--------
* Join logic against a small fixture ticker->sector CSV + a fake
  ``AccountSnapshot``/positions dict.
* Unmapped symbols degrade to ``"Unknown"`` — never dropped (CONSTRAINT #4).
* ``pct_of_equity`` across every returned bucket sums to ~100% of
  ``total_equity`` for a fully-invested portfolio.
* Empty positions -> empty result, no crash.
* Pure-function contract: no I/O beyond the cached CSV read; a missing/
  broken CSV degrades every symbol to "Unknown" rather than raising.
"""

from __future__ import annotations

import csv
from types import SimpleNamespace

import pytest

from engine.portfolio_exposure import (
    SectorExposure,
    compute_sector_exposure,
    reset_sector_map_cache,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Every test gets a clean module-level sector-map cache."""
    reset_sector_map_cache()
    yield
    reset_sector_map_cache()


def _write_csv(tmp_path, rows):
    path = tmp_path / "ticker_sectors.csv"
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["symbol", "sector"])
        for sym, sector in rows:
            writer.writerow([sym, sector])
    return str(path)


def _position(symbol: str, market_value: float):
    return SimpleNamespace(symbol=symbol, market_value=market_value)


def _snapshot(positions: dict, total_equity: float):
    return SimpleNamespace(positions=positions, total_equity=total_equity)


class TestComputeSectorExposure:
    def test_basic_join(self, tmp_path):
        csv_path = _write_csv(
            tmp_path, [("AAPL", "Technology"), ("JPM", "Financial Services")]
        )
        positions = {
            "AAPL": _position("AAPL", 6000.0),
            "JPM": _position("JPM", 4000.0),
        }
        snapshot = _snapshot(positions, total_equity=10000.0)

        result = compute_sector_exposure(snapshot, csv_path=csv_path)

        assert set(result.keys()) == {"Technology", "Financial Services"}
        assert isinstance(result["Technology"], SectorExposure)
        assert result["Technology"].net_market_value == pytest.approx(6000.0)
        assert result["Technology"].pct_of_equity == pytest.approx(0.6)
        assert result["Technology"].symbols == ["AAPL"]
        assert result["Financial Services"].pct_of_equity == pytest.approx(0.4)

    def test_unmapped_symbol_becomes_unknown_never_dropped(self, tmp_path):
        csv_path = _write_csv(tmp_path, [("AAPL", "Technology")])
        positions = {
            "AAPL": _position("AAPL", 5000.0),
            "ZZZZ": _position("ZZZZ", 5000.0),  # not in the sector map
        }
        snapshot = _snapshot(positions, total_equity=10000.0)

        result = compute_sector_exposure(snapshot, csv_path=csv_path)

        assert "Unknown" in result
        assert result["Unknown"].symbols == ["ZZZZ"]
        assert result["Unknown"].net_market_value == pytest.approx(5000.0)
        # ZZZZ is never silently excluded from the result set.
        all_symbols = {s for bucket in result.values() for s in bucket.symbols}
        assert "ZZZZ" in all_symbols

    def test_percentages_sum_to_100_for_fully_invested_portfolio(self, tmp_path):
        csv_path = _write_csv(
            tmp_path,
            [("AAPL", "Technology"), ("JPM", "Financial Services"), ("XOM", "Energy")],
        )
        positions = {
            "AAPL": _position("AAPL", 3000.0),
            "JPM": _position("JPM", 3000.0),
            "XOM": _position("XOM", 4000.0),
        }
        snapshot = _snapshot(positions, total_equity=10000.0)

        result = compute_sector_exposure(snapshot, csv_path=csv_path)

        total_pct = sum(s.pct_of_equity for s in result.values())
        assert total_pct == pytest.approx(1.0, abs=1e-9)

    def test_empty_positions_returns_empty_no_crash(self, tmp_path):
        csv_path = _write_csv(tmp_path, [("AAPL", "Technology")])
        snapshot = _snapshot({}, total_equity=0.0)

        result = compute_sector_exposure(snapshot, csv_path=csv_path)

        assert result == {}

    def test_none_positions_attribute_returns_empty(self, tmp_path):
        csv_path = _write_csv(tmp_path, [("AAPL", "Technology")])
        snapshot = SimpleNamespace(positions=None, total_equity=0.0)

        result = compute_sector_exposure(snapshot, csv_path=csv_path)

        assert result == {}

    def test_missing_csv_degrades_every_symbol_to_unknown(self, tmp_path):
        missing_path = str(tmp_path / "does_not_exist.csv")
        positions = {"AAPL": _position("AAPL", 1000.0)}
        snapshot = _snapshot(positions, total_equity=1000.0)

        result = compute_sector_exposure(snapshot, csv_path=missing_path)

        assert set(result.keys()) == {"Unknown"}
        assert result["Unknown"].symbols == ["AAPL"]

    def test_never_raises_on_malformed_position(self, tmp_path):
        csv_path = _write_csv(tmp_path, [("AAPL", "Technology")])
        # A position object missing market_value entirely -- getattr default
        # handles this rather than raising AttributeError.
        broken_position = SimpleNamespace(symbol="AAPL")
        snapshot = _snapshot({"AAPL": broken_position}, total_equity=1000.0)

        result = compute_sector_exposure(snapshot, csv_path=csv_path)

        assert "Technology" in result
        assert result["Technology"].net_market_value == 0.0

    def test_short_position_negative_market_value_preserved(self, tmp_path):
        # market_value already carries sign per PortfolioPosition's own
        # convention (quantity * current_price) -- a short position's
        # negative market value must flow straight through, not get abs()'d.
        csv_path = _write_csv(tmp_path, [("TSLA", "Consumer Cyclical")])
        positions = {"TSLA": _position("TSLA", -2000.0)}
        snapshot = _snapshot(positions, total_equity=10000.0)

        result = compute_sector_exposure(snapshot, csv_path=csv_path)

        assert result["Consumer Cyclical"].net_market_value == pytest.approx(-2000.0)
        assert result["Consumer Cyclical"].pct_of_equity == pytest.approx(-0.2)

    def test_zero_total_equity_avoids_division_by_zero(self, tmp_path):
        csv_path = _write_csv(tmp_path, [("AAPL", "Technology")])
        positions = {"AAPL": _position("AAPL", 1000.0)}
        snapshot = _snapshot(positions, total_equity=0.0)

        result = compute_sector_exposure(snapshot, csv_path=csv_path)

        assert result["Technology"].pct_of_equity == 0.0

    def test_multiple_symbols_same_sector_aggregate(self, tmp_path):
        csv_path = _write_csv(
            tmp_path, [("AAPL", "Technology"), ("MSFT", "Technology")]
        )
        positions = {
            "AAPL": _position("AAPL", 3000.0),
            "MSFT": _position("MSFT", 2000.0),
        }
        snapshot = _snapshot(positions, total_equity=5000.0)

        result = compute_sector_exposure(snapshot, csv_path=csv_path)

        assert len(result) == 1
        tech = result["Technology"]
        assert tech.net_market_value == pytest.approx(5000.0)
        assert sorted(tech.symbols) == ["AAPL", "MSFT"]

    def test_real_ticker_sectors_csv_loads(self):
        # Sanity check against the REAL forecasting/data/ticker_sectors.csv
        # default path -- AAPL should resolve to a real (non-Unknown) sector.
        positions = {"AAPL": _position("AAPL", 1000.0)}
        snapshot = _snapshot(positions, total_equity=1000.0)

        result = compute_sector_exposure(snapshot)

        assert "Technology" in result
        assert result["Technology"].symbols == ["AAPL"]

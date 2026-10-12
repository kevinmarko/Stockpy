"""Tests for scripts/build_ticker_sector_map.py and the committed
forecasting/data/ticker_sectors.csv it regenerates.

Split out of the former tests/test_backtest_sector_configs_cli.py when the
sector-config backtest pipeline (scripts/backtest_sector_configs.py) was
archived to legacy/ (forecasting rebuild F4 part 1). The CSV itself stays: it
is read by engine/portfolio_exposure.py and data/sneqr_quality_facts.py.
``build_ticker_sector_map.py`` is only smoke-tested for ``--help``, plus unit
tests of its ``fetch_ticker_sector`` helper with the yfinance call
monkeypatched (never requiring yfinance to be installed).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import build_ticker_sector_map as btsm  # noqa: E402

# The 11 yfinance sector names (formerly read from the archived
# backtest script's _DEFAULT_SECTOR_CONFIGS keys).
_KNOWN_SECTORS = (
    "Technology", "Consumer Cyclical", "Communication Services", "Healthcare",
    "Energy", "Financial Services", "Industrials", "Real Estate", "Utilities",
    "Consumer Defensive", "Basic Materials",
)


class TestBuildTickerSectorMapCLI:
    def test_help_exits_zero(self):
        with pytest.raises(SystemExit) as excinfo:
            btsm.main(["--help"])
        assert excinfo.value.code == 0

    def test_fetch_ticker_sector_monkeypatched_success(self, monkeypatch):
        """Monkeypatch at the yfinance import boundary so this test never
        requires yfinance to actually be installed."""

        class _FakeTicker:
            def __init__(self, symbol):
                self.info = {"sector": "Technology"}

        class _FakeYFinanceModule:
            Ticker = _FakeTicker

        monkeypatch.setitem(sys.modules, "yfinance", _FakeYFinanceModule())
        sector = btsm.fetch_ticker_sector("AAPL")
        assert sector == "Technology"

    def test_fetch_ticker_sector_missing_sector_returns_none(self, monkeypatch):
        class _FakeTicker:
            def __init__(self, symbol):
                self.info = {}

        class _FakeYFinanceModule:
            Ticker = _FakeTicker

        monkeypatch.setitem(sys.modules, "yfinance", _FakeYFinanceModule())
        sector = btsm.fetch_ticker_sector("ZZZZ")
        assert sector is None

    def test_fetch_ticker_sector_exception_returns_none(self, monkeypatch):
        class _FakeTicker:
            def __init__(self, symbol):
                raise RuntimeError("network down")

        class _FakeYFinanceModule:
            Ticker = _FakeTicker

        monkeypatch.setitem(sys.modules, "yfinance", _FakeYFinanceModule())
        sector = btsm.fetch_ticker_sector("BADTICKER")
        assert sector is None

    def test_build_ticker_sector_map_skips_missing_sectors_and_bad_tickers(self, monkeypatch):
        def fake_fetch(symbol):
            if symbol == "GOOD":
                return "Technology"
            if symbol == "BLANK":
                return None
            raise RuntimeError("boom")

        monkeypatch.setattr(btsm, "fetch_ticker_sector", fake_fetch)
        rows = btsm.build_ticker_sector_map(
            ["GOOD", "BLANK", "BROKEN"], sleep_seconds=0.0
        )
        assert rows == [{"symbol": "GOOD", "sector": "Technology"}]


# ---------------------------------------------------------------------------
# Committed seed CSV
# ---------------------------------------------------------------------------


class TestCommittedTickerSectorsCSV:
    CSV_PATH = _REPO_ROOT / "forecasting" / "data" / "ticker_sectors.csv"

    def test_parses_with_pandas_and_has_required_columns(self):
        df = pd.read_csv(self.CSV_PATH)
        assert "symbol" in df.columns
        assert "sector" in df.columns
        assert len(df) > 0

    def test_all_sectors_non_empty_strings(self):
        df = pd.read_csv(self.CSV_PATH)
        for value in df["symbol"]:
            assert isinstance(value, str) and value.strip()
        for value in df["sector"]:
            assert isinstance(value, str) and value.strip()

    def test_covers_at_least_two_symbols_per_known_sector(self):
        df = pd.read_csv(self.CSV_PATH)
        known_sectors = set(_KNOWN_SECTORS)
        counts = df["sector"].value_counts()

        # Every sector in the CSV must be a recognized yfinance sector name.
        for sector in df["sector"].unique():
            assert sector in known_sectors, f"unrecognized sector {sector!r} in seed CSV"

        # Every known sector must appear at least twice in the seed CSV.
        for sector in known_sectors:
            assert sector in counts.index, f"sector {sector!r} missing entirely from seed CSV"
            assert counts[sector] >= 2, f"sector {sector!r} has fewer than 2 symbols in seed CSV"

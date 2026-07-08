"""Tests for scripts/backtest_sector_configs.py and scripts/build_ticker_sector_map.py.

All tests here run fully offline. ``backtest_sector_configs.py --offline`` uses
deterministic synthetic price data (no network, no HistoricalStore/DataEngine
involvement); ``build_ticker_sector_map.py`` is only smoke-tested for
importability/``--help``, plus a unit test of its ``fetch_ticker_sector`` helper
with the yfinance call itself monkeypatched (never requiring yfinance to
actually be installed in this environment).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import backtest_sector_configs as bsc  # noqa: E402
from scripts import build_ticker_sector_map as btsm  # noqa: E402

# The committed 31-symbol seed CSV is deliberately small, but at the default
# BacktestConfig (lookback_days=750, step_days=21) it still produces ~32
# expanding-window anchors per (symbol, model, horizon) cell -- 31 symbols x 3
# models x 3 horizons x real ARIMA/Holt-Winters MLE fits is multiple minutes
# per CLI invocation. The CLI-invoking tests below exercise the exact same
# code path (main() -> synthesize_offline_price_data -> run_sector_backtest ->
# derive_sector_configs -> build_artifact -> write_artifact) against a much
# smaller 4-symbol/2-sector population and a coarser --step-days, so the test
# suite runs in seconds rather than minutes while still proving the whole
# pipeline end-to-end. TestCommittedTickerSectorsCSV below still validates the
# real, full committed CSV directly (no CLI run involved there).
_FAST_STEP_DAYS = "180"


@pytest.fixture
def tiny_ticker_sectors_csv(tmp_path):
    path = tmp_path / "tiny_ticker_sectors.csv"
    path.write_text(
        "symbol,sector\n"
        "AAPL,Technology\n"
        "MSFT,Technology\n"
        "XOM,Energy\n"
        "CVX,Energy\n"
    )
    return path


def _run_offline(tmp_path, ticker_sectors_csv, output_name="out.json", seed="7", extra_args=None):
    out_path = tmp_path / output_name
    argv = [
        "--offline",
        "--ticker-sectors", str(ticker_sectors_csv),
        "--output", str(out_path),
        "--seed", seed,
        "--step-days", _FAST_STEP_DAYS,
    ]
    if extra_args:
        argv.extend(extra_args)
    rc = bsc.main(argv)
    return rc, out_path


# ---------------------------------------------------------------------------
# backtest_sector_configs.py --offline
# ---------------------------------------------------------------------------


class TestOfflineCLIRun:
    def test_offline_run_exits_zero_and_writes_valid_json(self, tmp_path, tiny_ticker_sectors_csv):
        rc, out_path = _run_offline(tmp_path, tiny_ticker_sectors_csv)
        assert rc == 0
        assert out_path.exists()

        with out_path.open() as f:
            artifact = json.load(f)

        assert "schema_version" in artifact
        assert "sector_configs" in artifact
        assert "grid" in artifact
        assert "backtest" in artifact
        assert isinstance(artifact["sector_configs"], dict)
        assert isinstance(artifact["grid"], list)
        assert len(artifact["sector_configs"]) > 0

    def test_every_sector_key_is_a_real_yfinance_sector_name(self, tmp_path, tiny_ticker_sectors_csv):
        """Regression guard: a Wikipedia-GICS-vs-yfinance sector-name mismatch
        was explicitly called out as a risk in the design -- every derived
        sector key must be a member of the canonical yfinance sector-name set
        (``_DEFAULT_SECTOR_CONFIGS``'s keys, the well-known heuristic)."""
        rc, out_path = _run_offline(tmp_path, tiny_ticker_sectors_csv)
        assert rc == 0

        with out_path.open() as f:
            artifact = json.load(f)

        known_sectors = set(bsc._DEFAULT_SECTOR_CONFIGS.keys())
        for sector in artifact["sector_configs"]:
            assert sector in known_sectors, (
                f"sector_configs key {sector!r} is not a recognized yfinance "
                f"sector name (known: {sorted(known_sectors)})"
            )

    def test_grid_entries_have_valid_models_and_horizons(self, tmp_path, tiny_ticker_sectors_csv):
        rc, out_path = _run_offline(tmp_path, tiny_ticker_sectors_csv)
        assert rc == 0

        with out_path.open() as f:
            artifact = json.load(f)

        for cell in artifact["grid"]:
            assert cell["model"] in ("MC", "ARIMA", "HW")
            assert cell["horizon"] in (30, 60, 90)
            assert "mase" in cell
            assert "rmse" in cell
            assert "n_forecasts" in cell
            assert "n_symbols" in cell


class TestOfflineDeterminism:
    def test_same_seed_produces_identical_payload(self, tmp_path, tiny_ticker_sectors_csv):
        rc1, out1 = _run_offline(tmp_path, tiny_ticker_sectors_csv, output_name="out1.json", seed="123")
        rc2, out2 = _run_offline(tmp_path, tiny_ticker_sectors_csv, output_name="out2.json", seed="123")
        assert rc1 == 0
        assert rc2 == 0

        with out1.open() as f:
            artifact1 = json.load(f)
        with out2.open() as f:
            artifact2 = json.load(f)

        # Strip non-deterministic / timestamp fields before comparing.
        for artifact in (artifact1, artifact2):
            artifact.pop("generated_at", None)

        assert artifact1["sector_configs"] == artifact2["sector_configs"]
        assert artifact1["grid"] == artifact2["grid"]
        assert artifact1["schema_version"] == artifact2["schema_version"]

    def test_different_seed_can_differ(self, tmp_path, tiny_ticker_sectors_csv):
        # Not a strict requirement that outputs MUST differ (a tiny/quiet grid
        # could tie), but the run must still succeed and produce valid output
        # for a different seed -- exercised here mainly as a sanity check that
        # --seed is actually threaded through without crashing.
        rc, out_path = _run_offline(tmp_path, tiny_ticker_sectors_csv, seed="999")
        assert rc == 0
        with out_path.open() as f:
            artifact = json.load(f)
        assert len(artifact["sector_configs"]) > 0


class TestOfflineHelpers:
    def test_synthesize_offline_price_data_shape_contract(self):
        price_data = bsc.synthesize_offline_price_data(["AAPL", "MSFT"], n_bars=100, seed=1)
        assert set(price_data.keys()) == {"AAPL", "MSFT"}
        for symbol, df in price_data.items():
            assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
            assert isinstance(df.index, pd.DatetimeIndex)
            assert df.index.tz is None
            assert df.index.is_monotonic_increasing
            assert len(df) == 100
            assert (df["Close"] > 0).all()

    def test_synthesize_offline_price_data_is_deterministic(self):
        d1 = bsc.synthesize_offline_price_data(["AAPL"], n_bars=50, seed=5)
        d2 = bsc.synthesize_offline_price_data(["AAPL"], n_bars=50, seed=5)
        pd.testing.assert_frame_equal(d1["AAPL"], d2["AAPL"])

    def test_load_ticker_sectors_reads_seed_csv(self):
        mapping = bsc.load_ticker_sectors(bsc.DEFAULT_TICKER_SECTORS_PATH)
        assert len(mapping) > 0
        assert "AAPL" in mapping
        assert mapping["AAPL"] == "Technology"

    def test_load_ticker_sectors_missing_file_returns_empty_dict(self, tmp_path):
        mapping = bsc.load_ticker_sectors(tmp_path / "does_not_exist.csv")
        assert mapping == {}

    def test_main_aborts_cleanly_on_missing_ticker_sectors_file(self, tmp_path):
        rc = bsc.main([
            "--offline",
            "--ticker-sectors", str(tmp_path / "nope.csv"),
            "--output", str(tmp_path / "out.json"),
        ])
        assert rc != 0
        assert not (tmp_path / "out.json").exists()


# ---------------------------------------------------------------------------
# build_ticker_sector_map.py
# ---------------------------------------------------------------------------


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
        known_sectors = set(bsc._DEFAULT_SECTOR_CONFIGS.keys())
        counts = df["sector"].value_counts()

        # Every sector in the CSV must be a recognized yfinance sector name.
        for sector in df["sector"].unique():
            assert sector in known_sectors, f"unrecognized sector {sector!r} in seed CSV"

        # Every known sector must appear at least twice in the seed CSV.
        for sector in known_sectors:
            assert sector in counts.index, f"sector {sector!r} missing entirely from seed CSV"
            assert counts[sector] >= 2, f"sector {sector!r} has fewer than 2 symbols in seed CSV"

"""
InvestYo Quant Platform - EDGAR PIT Strategy Adapters Validation Test
=========================================================================
Runs the three production ``scripts.refresh_validations`` adapters that read
real SEC EDGAR point-in-time (PIT) fundamentals —
``_build_dividend_yield_adapter`` / ``_build_deep_value_adapter`` /
``_build_value_quality_adapter`` (registered as
``STRATEGY_REGISTRY["dividend_yield_edgar_pit"]`` /
``["deep_value_edgar_pit"]`` / ``["value_quality_edgar_pit"]``, joined to the
``dividend-income`` / ``deep-value`` / ``value-quality`` Pilots) — over real
historical price data plus the SAME committed EDGAR PIT fixture
``tests/fixtures/edgar_pit_fundamentals_sample.json`` that
``tests/test_validation_multifactor.py``'s
``test_value_quality_proxy_validation_harness_runs`` already proved this
mechanism against.

Self-contained (no shared conftest), matching this repo's existing
per-validation-test-file convention (``tests/test_validation_multifactor.py``
duplicates its own ``TICKERS``/``price_history``/PIT-store fixtures rather
than sharing them).

Honesty (CONSTRAINT #4): every well-formedness test asserts the harness
report is well-formed (finite Sharpe/MaxDD, ``deployable`` is a bool) but
NEVER that ``deployable is True`` — an 18-year, 10-name proxy with real,
occasionally sparse PIT coverage is not expected to clear the Sharpe/DSR
deployability bar on its own, and that is not what these tests enforce.

Dead-letter (CONSTRAINT #6): a genuinely EMPTY store (the real fresh-clone
case — ``scripts/backfill_edgar_fundamentals.py`` never run) must degrade
every adapter to a valid-shaped, all-NaN-factor result, never raise.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yfinance as yf

from execution.cost_model import TieredCostModel
from validation.harness import StrategyValidationHarness
from scripts.refresh_validations import (
    _build_deep_value_adapter,
    _build_dividend_yield_adapter,
    _build_value_quality_adapter,
    _pit_asof_frame,
)

_EDGAR_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "edgar_pit_fundamentals_sample.json"

# Downloads real multi-ticker price history live from Yahoo Finance in its
# module-scoped fixtures — network-dependent, deselected in CI via
# ``pytest -m "not network"``.
pytestmark = pytest.mark.network

# Matches STRATEGY_REGISTRY's declared universe for all three EDGAR-based
# adapters, and the committed fixture's exact ticker coverage.
TICKERS = ["AAPL", "JNJ", "XOM", "KO", "JPM", "PG", "INTC", "T", "GE", "F"]


@pytest.fixture(scope="module")
def price_history() -> dict:
    data = {}
    for ticker in TICKERS:
        df = yf.download(ticker, start="2005-01-01", end="2023-12-31", progress=False)
        if df is not None and not df.empty:
            df.index = pd.to_datetime(df.index)
            data[ticker] = df
    assert len(data) >= 5, "Failed to download enough tickers for a meaningful cross-section"
    return data


def _closes_frame(price_history: dict) -> pd.DataFrame:
    closes = {t: df["Close"].squeeze() for t, df in price_history.items()}
    common_index = None
    for s in closes.values():
        common_index = s.index if common_index is None else common_index.intersection(s.index)
    assert common_index is not None and len(common_index) > 300
    return pd.DataFrame({t: s.reindex(common_index) for t, s in closes.items()})


@pytest.fixture(scope="module")
def real_pit_fundamentals_store(tmp_path_factory):
    """Real SEC EDGAR PIT fundamentals for the test universe, loaded from the
    same checked-in fixture tests/test_validation_multifactor.py already
    proves this mechanism against. Every row is a genuine value from a real
    SEC filing, keyed by the date it actually became public (report_date) —
    never fabricated (CONSTRAINT #4)."""
    from data.historical_store import HistoricalStore

    with open(_EDGAR_FIXTURE_PATH, "r", encoding="utf-8") as f:
        fixture = json.load(f)

    db_path = tmp_path_factory.mktemp("db") / "edgar_pit.db"
    store = HistoricalStore(db_path=str(db_path))

    typed_keys = (
        "pe_ratio", "pb_ratio", "roe", "dividend_yield",
        "market_cap", "eps", "operating_margin", "debt_to_equity",
    )
    for row in fixture["rows"]:
        typed = {k: row.get(k) for k in typed_keys}
        store.upsert_fundamentals_pit(
            row["symbol"], typed, typed,
            report_date=row["report_date"], source="edgar_fixture",
        )
    return store


@pytest.fixture()
def empty_fundamentals_store(tmp_path):
    """A genuinely empty HistoricalStore — the real fresh-clone case where
    scripts/backfill_edgar_fundamentals.py has never been run."""
    from data.historical_store import HistoricalStore

    return HistoricalStore(db_path=str(tmp_path / "empty.db"))


def _run_harness(X, y, precomputed, name, tmp_path):
    def strategy_fn(X_train, y_train, X_test, y_test):
        return [
            {
                "params": pname,
                "train_returns": returns.loc[returns.index.intersection(y_train.index)],
                "test_returns": returns.loc[returns.index.intersection(y_test.index)],
                "turnover": 0.05,
            }
            for pname, returns in precomputed.items()
        ]

    cost_model = TieredCostModel()

    def mock_universe_fn(as_of_date):
        return TICKERS

    harness = StrategyValidationHarness(
        strategy_fn=strategy_fn,
        universe_fn=mock_universe_fn,
        cost_model=cost_model,
        n_cpcv_splits=10,
        n_test_splits=2,
        reports_dir=str(tmp_path),
    )
    return harness.run(
        start_date=str(X.index[0].date()),
        end_date=str(X.index[-1].date()),
        X=X,
        y=y,
        strategy_name=name,
    )


class TestWellFormedness:
    """Honest verdict — printed, never tuned to force a pass (CONSTRAINT #4)."""

    def test_dividend_yield_edgar_pit_runs(
        self, price_history, real_pit_fundamentals_store, tmp_path, monkeypatch
    ):
        closes = _closes_frame(price_history)
        monkeypatch.setattr(
            "data.historical_store.HistoricalStore",
            lambda *a, **kw: real_pit_fundamentals_store,
        )
        X, y, precomputed = _build_dividend_yield_adapter(closes)
        assert not X.empty and not y.empty and precomputed

        report = _run_harness(X, y, precomputed, "DividendYield_EdgarPit_Test", tmp_path)
        print("\n--- DIVIDEND YIELD (REAL EDGAR PIT DATA) REPORT ---")
        print(f"Sharpe: {report.sharpe:.3f}  MaxDD: {report.max_dd * 100:.2f}%  "
              f"DSR: {report.dsr:.4f}  PBO: {report.pbo:.4f}  Deployable: {report.deployable}")
        assert not np.isnan(report.sharpe)
        assert not np.isnan(report.max_dd)
        assert isinstance(report.deployable, bool)

    def test_deep_value_edgar_pit_runs(
        self, price_history, real_pit_fundamentals_store, tmp_path, monkeypatch
    ):
        closes = _closes_frame(price_history)
        monkeypatch.setattr(
            "data.historical_store.HistoricalStore",
            lambda *a, **kw: real_pit_fundamentals_store,
        )
        X, y, precomputed = _build_deep_value_adapter(closes)
        assert not X.empty and not y.empty and precomputed

        report = _run_harness(X, y, precomputed, "DeepValue_EdgarPit_Test", tmp_path)
        print("\n--- DEEP VALUE / P-B (REAL EDGAR PIT DATA) REPORT ---")
        print(f"Sharpe: {report.sharpe:.3f}  MaxDD: {report.max_dd * 100:.2f}%  "
              f"DSR: {report.dsr:.4f}  PBO: {report.pbo:.4f}  Deployable: {report.deployable}")
        assert not np.isnan(report.sharpe)
        assert not np.isnan(report.max_dd)
        assert isinstance(report.deployable, bool)

    def test_value_quality_edgar_pit_runs(
        self, price_history, real_pit_fundamentals_store, tmp_path, monkeypatch
    ):
        closes = _closes_frame(price_history)
        monkeypatch.setattr(
            "data.historical_store.HistoricalStore",
            lambda *a, **kw: real_pit_fundamentals_store,
        )
        X, y, precomputed = _build_value_quality_adapter(closes)
        assert not X.empty and not y.empty and precomputed

        report = _run_harness(X, y, precomputed, "ValueQuality_EdgarPit_Test", tmp_path)
        print("\n--- VALUE + QUALITY (REAL EDGAR PIT DATA) REPORT ---")
        print(f"Sharpe: {report.sharpe:.3f}  MaxDD: {report.max_dd * 100:.2f}%  "
              f"DSR: {report.dsr:.4f}  PBO: {report.pbo:.4f}  Deployable: {report.deployable}")
        assert not np.isnan(report.sharpe)
        assert not np.isnan(report.max_dd)
        assert isinstance(report.deployable, bool)


class TestPitAlignmentIsLookaheadFree:
    def test_pit_asof_frame_no_lookahead(self, tmp_path):
        """Perturbing a PIT fundamentals row's value strictly AFTER a cutoff
        date must not change the forward-filled value AT OR BEFORE that
        cutoff (merge_asof(direction='backward') is inherently causal, but
        this pins the guarantee against regression). Uses two independent,
        function-scoped stores (never mutates the shared module-scoped
        ``real_pit_fundamentals_store`` fixture, which the well-formedness
        tests above also depend on)."""
        from data.historical_store import HistoricalStore

        common_index = pd.date_range("2015-01-01", "2020-12-31", freq="B")
        cutoff = pd.Timestamp("2018-06-01")

        def _build_store(db_name: str, extra_row_value=None) -> "HistoricalStore":
            store = HistoricalStore(db_path=str(tmp_path / db_name))
            for i, d in enumerate(pd.date_range("2015-06-01", "2018-01-01", freq="QE")):
                store.upsert_fundamentals_pit(
                    "AAPL",
                    {"pb_ratio": 3.0 + i * 0.1},
                    {"pb_ratio": 3.0 + i * 0.1},
                    report_date=d.strftime("%Y-%m-%d"),
                    source="test",
                )
            if extra_row_value is not None:
                store.upsert_fundamentals_pit(
                    "AAPL",
                    {"pb_ratio": extra_row_value},
                    {"pb_ratio": extra_row_value},
                    report_date="2019-01-01",
                    source="test",
                )
            return store

        baseline_store = _build_store("baseline.db")
        perturbed_store = _build_store("perturbed.db", extra_row_value=999999.0)

        baseline = _pit_asof_frame(baseline_store, ["AAPL"], common_index)["AAPL"]["pb_ratio"]
        perturbed = _pit_asof_frame(perturbed_store, ["AAPL"], common_index)["AAPL"]["pb_ratio"]

        pre_cutoff = common_index[common_index <= cutoff]
        pd.testing.assert_series_equal(
            baseline.loc[pre_cutoff], perturbed.loc[pre_cutoff], check_names=False
        )
        # Sanity: the perturbation DID change something after the 2019-01-01
        # filing date, proving the test would have caught a lookahead bug.
        post_filing = common_index[common_index > pd.Timestamp("2019-01-01")]
        assert (perturbed.loc[post_filing] == 999999.0).any()


class TestEmptyStoreDeadLetter:
    """The real fresh-clone case: no EDGAR backfill has ever run."""

    def test_dividend_yield_degrades_on_empty_store(
        self, price_history, empty_fundamentals_store, monkeypatch
    ):
        closes = _closes_frame(price_history)
        monkeypatch.setattr(
            "data.historical_store.HistoricalStore",
            lambda *a, **kw: empty_fundamentals_store,
        )
        X, y, precomputed = _build_dividend_yield_adapter(closes)
        assert not X.empty and not y.empty and precomputed
        assert not y.isna().any()
        assert not precomputed["DividendYield_TopHalf"].isna().any()

    def test_deep_value_degrades_on_empty_store(
        self, price_history, empty_fundamentals_store, monkeypatch
    ):
        closes = _closes_frame(price_history)
        monkeypatch.setattr(
            "data.historical_store.HistoricalStore",
            lambda *a, **kw: empty_fundamentals_store,
        )
        X, y, precomputed = _build_deep_value_adapter(closes)
        assert not X.empty and not y.empty and precomputed
        assert not y.isna().any()
        assert not precomputed["DeepValue_TopHalf"].isna().any()

    def test_value_quality_degrades_on_empty_store(
        self, price_history, empty_fundamentals_store, monkeypatch
    ):
        closes = _closes_frame(price_history)
        monkeypatch.setattr(
            "data.historical_store.HistoricalStore",
            lambda *a, **kw: empty_fundamentals_store,
        )
        X, y, precomputed = _build_value_quality_adapter(closes)
        assert not X.empty and not y.empty and precomputed
        assert not y.isna().any()
        assert not precomputed["ValueQuality_TopHalf"].isna().any()
        # No PIT coverage at all -> every symbol's factor is NaN -> the
        # composite legitimately carries no cross-sectional signal.
        assert (X["Value_Composite"].abs() < 1e-6).all()
        assert (X["Quality_Composite"].abs() < 1e-6).all()

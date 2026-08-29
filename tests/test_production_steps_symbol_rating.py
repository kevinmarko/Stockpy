"""
tests/test_production_steps_symbol_rating.py
===============================================
Unit tests for pipeline/production_steps.py::_apply_symbol_rating_columns --
the diagnostic Symbol_Rating_Consecutive_Bad_Cycles / Symbol_Rating_Excluded
column writeback.

Added alongside the F5 fix in docs/module_efficiency_redundancy_audit.md:
this function previously issued one SymbolRatingStore.get_consecutive_bad_cycles()
query per ticker via dashboard_df['Symbol'].map(...) plus a row-wise
dashboard_df.apply(_excluded, axis=1) -- both replaced with a single
SymbolRatingStore.get_consecutive_bad_cycles_bulk() call and vectorized
numpy comparisons. No test previously exercised this function directly (it
had zero dedicated coverage before this file); these tests are both new
coverage AND the equivalence proof for the rewrite.

Deliberately targets the module-level `_apply_symbol_rating_columns`
function directly (same convention as
tests/test_production_steps_sector_heat.py) rather than going through
StrategyEvalStep.run(), which imports main_orchestrator's full heavy engine
chain -- keeps this suite importable without tensorflow/statsmodels/etc.

SymbolRatingStore is lazily imported *inside* the function body
(`from rating.symbol_rating_store import SymbolRatingStore`), so patching
happens on the source module attribute (rating.symbol_rating_store.SymbolRatingStore),
matching tests/test_symbol_rating_wiring.py's established pattern -- a
plain module-scope patch of pipeline.production_steps would be a no-op
here, since the name isn't bound at that scope until the function runs.
A tmp_path-backed SQLite file (not `sqlite:///:memory:`) is used so a
write-mode store (seeding the fixture) and the function's own internal
readonly=True store see the same data -- two separate `:memory:` engines
would each get their own empty database.
"""
from __future__ import annotations

import functools

import numpy as np
import pandas as pd
import pytest

import rating.symbol_rating_store as rating_store_mod
from pipeline.production_steps import _apply_symbol_rating_columns
from rating.symbol_rating_store import SymbolRatingStore


def _event(symbol="AAPL", tier="BAD", is_held=False, score=20.0):
    return {
        "symbol": symbol,
        "score": score,
        "action_signal": "RISK REDUCE" if tier == "BAD" else "HOLD",
        "tier": tier,
        "is_held": is_held,
    }


@pytest.fixture
def rating_db(tmp_path, monkeypatch):
    """Seeds a tmp_path-backed SQLite file with rating history via a
    write-mode store, then monkeypatches SymbolRatingStore so
    _apply_symbol_rating_columns's internal readonly=True construction
    resolves to that same file rather than the real shared DB."""
    db_path = tmp_path / "symbol_rating.db"
    db_url = f"sqlite:///{db_path}"
    writer = SymbolRatingStore(db_url=db_url)
    monkeypatch.setattr(
        rating_store_mod, "SymbolRatingStore", functools.partial(SymbolRatingStore, db_url=db_url)
    )
    return writer


def _df(rows):
    return pd.DataFrame(rows)


class TestApplySymbolRatingColumns:
    def test_disabled_leaves_defaults_with_no_store_construction(self, monkeypatch):
        monkeypatch.setattr("settings.settings.SYMBOL_RATING_ENABLED", False)

        def _boom(*a, **k):
            raise AssertionError("SymbolRatingStore must not be constructed when disabled")

        monkeypatch.setattr(rating_store_mod, "SymbolRatingStore", _boom)

        df = _df([{"Symbol": "AAPL", "Robinhood Shares": 0.0}])
        _apply_symbol_rating_columns(df)

        assert df["Symbol_Rating_Consecutive_Bad_Cycles"].tolist() == [0.0]
        assert df["Symbol_Rating_Excluded"].tolist() == ["No"]

    def test_empty_dataframe_is_a_noop(self, rating_db):
        df = pd.DataFrame(columns=["Symbol", "Robinhood Shares"])
        _apply_symbol_rating_columns(df)
        assert df.empty

    def test_unheld_symbol_past_threshold_is_excluded(self, rating_db):
        for _ in range(5):
            rating_db.record_ratings([_event("AAPL", tier="BAD")])

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("settings.settings.SYMBOL_RATING_DROP_THRESHOLD_CYCLES", 3)
            df = _df([{"Symbol": "AAPL", "Robinhood Shares": 0.0}])
            _apply_symbol_rating_columns(df)

        assert df["Symbol_Rating_Consecutive_Bad_Cycles"].tolist() == [5.0]
        assert df["Symbol_Rating_Excluded"].tolist() == ["Yes"]

    def test_held_symbol_never_excluded_regardless_of_streak(self, rating_db):
        """Matches the pre-rewrite _excluded closure's behavior exactly:
        is_held short-circuits to "No" before the threshold comparison even
        runs, no matter how bad the streak is."""
        for _ in range(10):
            rating_db.record_ratings([_event("AAPL", tier="BAD")])

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("settings.settings.SYMBOL_RATING_DROP_THRESHOLD_CYCLES", 3)
            df = _df([{"Symbol": "AAPL", "Robinhood Shares": 100.0}])
            _apply_symbol_rating_columns(df)

        assert df["Symbol_Rating_Consecutive_Bad_Cycles"].tolist() == [10.0]
        assert df["Symbol_Rating_Excluded"].tolist() == ["No"]

    def test_symbol_below_threshold_is_not_excluded(self, rating_db):
        for _ in range(2):
            rating_db.record_ratings([_event("AAPL", tier="BAD")])

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("settings.settings.SYMBOL_RATING_DROP_THRESHOLD_CYCLES", 5)
            df = _df([{"Symbol": "AAPL", "Robinhood Shares": 0.0}])
            _apply_symbol_rating_columns(df)

        assert df["Symbol_Rating_Consecutive_Bad_Cycles"].tolist() == [2.0]
        assert df["Symbol_Rating_Excluded"].tolist() == ["No"]

    def test_symbol_with_no_history_defaults_to_zero_not_excluded(self, rating_db):
        df = _df([{"Symbol": "NOHISTORY", "Robinhood Shares": 0.0}])
        _apply_symbol_rating_columns(df)

        assert df["Symbol_Rating_Consecutive_Bad_Cycles"].tolist() == [0.0]
        assert df["Symbol_Rating_Excluded"].tolist() == ["No"]

    def test_nan_robinhood_shares_treated_as_not_held(self, rating_db):
        """Reproduces the exact NaN edge case the vectorized rewrite had to
        preserve: the original row-wise `float(x) or 0.0` guard makes
        `float(NaN) > 0` False (NaN comparisons are always False in Python),
        so a NaN share count must NOT be treated as "held"."""
        for _ in range(5):
            rating_db.record_ratings([_event("AAPL", tier="BAD")])

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("settings.settings.SYMBOL_RATING_DROP_THRESHOLD_CYCLES", 3)
            df = _df([{"Symbol": "AAPL", "Robinhood Shares": float("nan")}])
            _apply_symbol_rating_columns(df)

        assert df["Symbol_Rating_Excluded"].tolist() == ["Yes"]

    def test_missing_robinhood_shares_column_treated_as_not_held(self, rating_db):
        for _ in range(5):
            rating_db.record_ratings([_event("AAPL", tier="BAD")])

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("settings.settings.SYMBOL_RATING_DROP_THRESHOLD_CYCLES", 3)
            df = _df([{"Symbol": "AAPL"}])
            _apply_symbol_rating_columns(df)

        assert df["Symbol_Rating_Excluded"].tolist() == ["Yes"]

    def test_multi_symbol_universe_issues_one_bulk_query_not_n(self, rating_db, monkeypatch):
        """The actual regression this PR guards against: N tickers must
        resolve via ONE get_consecutive_bad_cycles_bulk() call, not N calls
        to get_consecutive_bad_cycles()."""
        for _ in range(5):
            rating_db.record_ratings([_event("AAPL", tier="BAD")])
        rating_db.record_ratings([_event("MSFT", tier="GOOD")])

        call_count = {"n": 0}
        real_bulk = SymbolRatingStore.get_consecutive_bad_cycles_bulk

        def _counting_bulk(self, symbols):
            call_count["n"] += 1
            return real_bulk(self, symbols)

        monkeypatch.setattr(SymbolRatingStore, "get_consecutive_bad_cycles_bulk", _counting_bulk)

        def _boom_per_symbol(self, symbol):
            raise AssertionError(
                "get_consecutive_bad_cycles (per-symbol) must not be called "
                "-- the N+1 pattern this PR removes"
            )

        monkeypatch.setattr(SymbolRatingStore, "get_consecutive_bad_cycles", _boom_per_symbol)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("settings.settings.SYMBOL_RATING_DROP_THRESHOLD_CYCLES", 3)
            df = _df([
                {"Symbol": "AAPL", "Robinhood Shares": 0.0},
                {"Symbol": "MSFT", "Robinhood Shares": 0.0},
                {"Symbol": "GOOG", "Robinhood Shares": 0.0},
            ])
            _apply_symbol_rating_columns(df)

        assert call_count["n"] == 1
        assert df["Symbol_Rating_Consecutive_Bad_Cycles"].tolist() == [5.0, 0.0, 0.0]
        assert df["Symbol_Rating_Excluded"].tolist() == ["Yes", "No", "No"]

    def test_output_columns_are_numeric_and_string_dtype_not_object_of_python_bools(self, rating_db):
        """Guards against a subtle np.where dtype regression: the Excluded
        column must contain plain Python/numpy strings ("Yes"/"No"), not
        numpy bool_ or another type that would break the Sheets/HTML writer
        expecting a string column."""
        df = _df([{"Symbol": "AAPL", "Robinhood Shares": 0.0}])
        _apply_symbol_rating_columns(df)

        assert all(isinstance(v, str) for v in df["Symbol_Rating_Excluded"])
        assert df["Symbol_Rating_Consecutive_Bad_Cycles"].dtype == np.float64

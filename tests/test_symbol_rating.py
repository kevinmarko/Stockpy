"""Tests for rating/symbol_rating.py (pure classification logic) and
rating/symbol_rating_store.py (the durable symbol_rating_events DB table).

Mirrors tests/test_cap_audit_store.py's conventions (in-memory SQLite for
CRUD, a tmp_path-backed file DB for readonly=True, missing-table degrade)."""

from datetime import datetime, timezone

import pytest

from rating.symbol_rating import classify_tier, should_exclude
from rating.symbol_rating_store import SymbolRatingStore


# ---------------------------------------------------------------------------
# rating/symbol_rating.py -- pure classification logic
# ---------------------------------------------------------------------------


class TestClassifyTier:
    def test_score_below_threshold_is_bad(self):
        assert classify_tier(20.0, threshold=35.0) == "BAD"

    def test_score_above_threshold_is_good(self):
        assert classify_tier(50.0, threshold=35.0) == "GOOD"

    def test_score_exactly_at_threshold_is_good(self):
        """Boundary: strictly < threshold is BAD, so a score equal to the
        threshold is GOOD (matches strategy_engine.py's `35 <= final_score <
        55` HOLD bucket -- 35 itself is not RISK REDUCE)."""
        assert classify_tier(35.0, threshold=35.0) == "GOOD"

    def test_score_just_below_threshold_is_bad(self):
        assert classify_tier(34.999, threshold=35.0) == "BAD"

    def test_score_zero_is_bad_given_positive_threshold(self):
        assert classify_tier(0.0, threshold=35.0) == "BAD"

    def test_score_100_is_good(self):
        assert classify_tier(100.0, threshold=35.0) == "GOOD"

    def test_nan_score_is_good_never_auto_excluded_on_missing_data(self):
        assert classify_tier(float("nan"), threshold=35.0) == "GOOD"

    def test_positive_infinity_is_good(self):
        assert classify_tier(float("inf"), threshold=35.0) == "GOOD"

    def test_negative_infinity_is_good_not_bad(self):
        """A non-finite value must never be silently treated as an extreme
        BAD score either -- CONSTRAINT #4/#6: missing/invalid data must
        classify as GOOD (never auto-exclude), regardless of sign."""
        assert classify_tier(float("-inf"), threshold=35.0) == "GOOD"

    def test_zero_threshold(self):
        assert classify_tier(-1.0, threshold=0.0) == "BAD"
        assert classify_tier(0.0, threshold=0.0) == "GOOD"

    def test_result_type_is_literal_string(self):
        result = classify_tier(10.0, threshold=35.0)
        assert result in ("GOOD", "BAD")
        assert isinstance(result, str)


class TestShouldExclude:
    def test_held_symbol_never_excluded_regardless_of_streak(self):
        assert should_exclude(consecutive_bad_cycles=999, threshold_cycles=5, is_held=True) is False

    def test_held_symbol_never_excluded_at_exact_threshold(self):
        assert should_exclude(consecutive_bad_cycles=5, threshold_cycles=5, is_held=True) is False

    def test_held_symbol_never_excluded_with_zero_threshold(self):
        """Even a pathological threshold_cycles=0 must not exclude a held
        position -- is_held is checked first and short-circuits."""
        assert should_exclude(consecutive_bad_cycles=100, threshold_cycles=0, is_held=True) is False

    def test_non_held_excluded_at_or_above_threshold(self):
        assert should_exclude(consecutive_bad_cycles=5, threshold_cycles=5, is_held=False) is True
        assert should_exclude(consecutive_bad_cycles=6, threshold_cycles=5, is_held=False) is True

    def test_non_held_not_excluded_below_threshold(self):
        assert should_exclude(consecutive_bad_cycles=4, threshold_cycles=5, is_held=False) is False

    def test_non_held_zero_streak_not_excluded(self):
        assert should_exclude(consecutive_bad_cycles=0, threshold_cycles=5, is_held=False) is False

    def test_result_is_bool(self):
        result = should_exclude(consecutive_bad_cycles=5, threshold_cycles=5, is_held=False)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# rating/symbol_rating_store.py -- durable rating history
# ---------------------------------------------------------------------------


def _event(symbol="AAPL", tier="BAD", is_held=False, **overrides) -> dict:
    defaults = dict(
        symbol=symbol,
        score=20.0 if tier == "BAD" else 60.0,
        action_signal="RISK REDUCE" if tier == "BAD" else "BUY",
        tier=tier,
        is_held=is_held,
    )
    defaults.update(overrides)
    return defaults


class TestRecordAndReadRoundTrip:
    def test_record_and_get_recent_round_trip(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        store.record_ratings([_event()], cycle_id="cycle-1")

        rows = store.get_recent(limit=10)
        assert len(rows) == 1
        row = rows[0]
        assert row["symbol"] == "AAPL"
        assert row["cycle_id"] == "cycle-1"
        assert row["tier"] == "BAD"
        assert row["action_signal"] == "RISK REDUCE"
        assert row["is_held"] is False
        assert row["score"] == pytest.approx(20.0)
        assert row["id"] is not None
        assert row["timestamp"] is not None

    def test_record_ratings_is_a_noop_on_empty_list(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        store.record_ratings([], cycle_id="cycle-1")
        assert store.get_recent(limit=10) == []

    def test_symbol_uppercased_on_write(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        store.record_ratings([_event(symbol="aapl")])
        rows = store.get_recent(limit=10)
        assert rows[0]["symbol"] == "AAPL"

    def test_invalid_tier_raises_value_error_not_silently_coerced(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        with pytest.raises(ValueError):
            store.record_ratings([_event(tier="TERRIBLE")])

    def test_record_ratings_writes_whole_cycle_in_one_call(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        store.record_ratings(
            [_event("AAPL"), _event("MSFT", tier="GOOD"), _event("GOOG")],
            cycle_id="cycle-42",
        )
        rows = store.get_recent(limit=10)
        assert len(rows) == 3
        assert all(r["cycle_id"] == "cycle-42" for r in rows)

    def test_get_recent_most_recent_first(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        store.record_ratings([_event("OLD")])
        store.record_ratings([_event("NEW")])

        rows = store.get_recent(limit=10)
        assert [r["symbol"] for r in rows] == ["NEW", "OLD"]

    def test_get_recent_filters_by_symbol(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        store.record_ratings([_event("AAPL"), _event("MSFT")])
        rows = store.get_recent(symbol="AAPL", limit=10)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "AAPL"

    def test_get_recent_respects_limit(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        for i in range(5):
            store.record_ratings([_event(f"SYM{i}")])
        rows = store.get_recent(limit=2)
        assert len(rows) == 2


class TestGetConsecutiveBadCycles:
    def test_counts_unbroken_recent_run(self):
        """3 BAD cycles, most recent first, then a GOOD cycle further back --
        the consecutive count must stop at the break, not count all 4."""
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        store.record_ratings([_event("AAPL", tier="GOOD")])
        store.record_ratings([_event("AAPL", tier="BAD")])
        store.record_ratings([_event("AAPL", tier="BAD")])
        store.record_ratings([_event("AAPL", tier="BAD")])

        assert store.get_consecutive_bad_cycles("AAPL") == 3

    def test_zero_when_most_recent_is_good(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        store.record_ratings([_event("AAPL", tier="BAD")])
        store.record_ratings([_event("AAPL", tier="GOOD")])

        assert store.get_consecutive_bad_cycles("AAPL") == 0

    def test_zero_with_no_history(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        assert store.get_consecutive_bad_cycles("NOSUCHSYMBOL") == 0

    def test_a_good_row_resets_the_streak_then_new_bad_rows_count_again(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        store.record_ratings([_event("AAPL", tier="BAD")])
        store.record_ratings([_event("AAPL", tier="BAD")])
        store.record_ratings([_event("AAPL", tier="GOOD")])
        store.record_ratings([_event("AAPL", tier="BAD")])

        assert store.get_consecutive_bad_cycles("AAPL") == 1

    def test_symbol_lookup_is_case_insensitive(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        store.record_ratings([_event("AAPL", tier="BAD")])
        assert store.get_consecutive_bad_cycles("aapl") == 1

    def test_degrades_to_zero_on_nonexistent_db_path(self, tmp_path):
        bad_path = tmp_path / "does" / "not" / "exist" / "db.sqlite"
        # readonly=True against a path whose parent directories don't exist
        # -- construction itself should not raise (create_readonly_db_engine
        # does not eagerly connect), but the query must degrade to 0.
        store = SymbolRatingStore(db_url=f"sqlite:///{bad_path}", readonly=True)
        assert store.get_consecutive_bad_cycles("AAPL") == 0

    def test_degrades_to_zero_on_missing_table(self, tmp_path):
        db_path = tmp_path / "never_written.db"
        db_path.touch()
        reader = SymbolRatingStore(db_url=f"sqlite:///{db_path}", readonly=True)
        assert reader.get_consecutive_bad_cycles("AAPL") == 0


class TestGetExcludedSymbols:
    def test_respects_cycle_threshold(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        for _ in range(3):
            store.record_ratings([_event("AAPL", tier="BAD")])
        for _ in range(5):
            store.record_ratings([_event("MSFT", tier="BAD")])

        excluded = store.get_excluded_symbols(threshold_cycles=5)
        assert excluded == {"MSFT"}

    def test_held_symbol_excluded_from_exclusion_set_regardless_of_streak(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        for _ in range(10):
            store.record_ratings([_event("AAPL", tier="BAD", is_held=True)])

        excluded = store.get_excluded_symbols(threshold_cycles=5)
        assert "AAPL" not in excluded
        assert excluded == set()

    def test_is_held_check_uses_most_recent_row(self):
        """A symbol that WAS held but has since been sold (most recent row
        is_held=False) is eligible for exclusion again."""
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        store.record_ratings([_event("AAPL", tier="BAD", is_held=True)])
        for _ in range(5):
            store.record_ratings([_event("AAPL", tier="BAD", is_held=False)])

        excluded = store.get_excluded_symbols(threshold_cycles=5)
        assert excluded == {"AAPL"}

    def test_known_symbols_param_scopes_the_scan(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        for _ in range(5):
            store.record_ratings([_event("AAPL", tier="BAD")])
            store.record_ratings([_event("MSFT", tier="BAD")])

        excluded = store.get_excluded_symbols(threshold_cycles=5, known_symbols=["AAPL"])
        assert excluded == {"AAPL"}

    def test_empty_when_no_symbol_meets_threshold(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        store.record_ratings([_event("AAPL", tier="BAD")])
        assert store.get_excluded_symbols(threshold_cycles=5) == set()

    def test_empty_with_no_history_at_all(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        assert store.get_excluded_symbols(threshold_cycles=5) == set()

    def test_degrades_to_empty_set_on_missing_table(self, tmp_path):
        db_path = tmp_path / "never_written.db"
        db_path.touch()
        reader = SymbolRatingStore(db_url=f"sqlite:///{db_path}", readonly=True)
        assert reader.get_excluded_symbols(threshold_cycles=5) == set()

    def test_return_type_is_a_set(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        for _ in range(5):
            store.record_ratings([_event("AAPL", tier="BAD")])
        excluded = store.get_excluded_symbols(threshold_cycles=5)
        assert isinstance(excluded, set)


class TestReinclude:
    def test_reinclude_clears_an_excluded_symbol(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        for _ in range(5):
            store.record_ratings([_event("AAPL", tier="BAD")])
        assert store.get_consecutive_bad_cycles("AAPL") == 5
        assert store.get_excluded_symbols(threshold_cycles=5) == {"AAPL"}

        store.reinclude("AAPL")

        assert store.get_consecutive_bad_cycles("AAPL") == 0
        assert store.get_excluded_symbols(threshold_cycles=5) == set()

    def test_reinclude_preserves_prior_history(self):
        """reinclude must not delete the real history -- only add a new
        GOOD row masking the streak forward."""
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        store.record_ratings([_event("AAPL", tier="BAD")])
        store.reinclude("AAPL")

        rows = store.get_recent(symbol="AAPL", limit=10)
        assert len(rows) == 2
        assert rows[0]["tier"] == "GOOD"
        assert rows[0]["cycle_id"] == "manual_reinclude"
        assert rows[1]["tier"] == "BAD"

    def test_reinclude_symbol_uppercased(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        store.record_ratings([_event("AAPL", tier="BAD")])
        store.reinclude("aapl")
        rows = store.get_recent(symbol="AAPL", limit=10)
        assert rows[0]["symbol"] == "AAPL"

    def test_reinclude_raises_on_readonly_store(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'ratings.db'}"
        SymbolRatingStore(db_url=db_url)  # write-mode: creates the schema first
        reader = SymbolRatingStore(db_url=db_url, readonly=True)
        with pytest.raises(RuntimeError):
            reader.reinclude("AAPL")


class TestReadonlyMode:
    def test_readonly_store_reads_data_written_by_a_write_mode_store(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'ratings.db'}"
        writer = SymbolRatingStore(db_url=db_url)
        writer.record_ratings([_event()])

        reader = SymbolRatingStore(db_url=db_url, readonly=True)
        rows = reader.get_recent(limit=10)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "AAPL"

    def test_readonly_store_write_raises_rather_than_fabricate_success(self, tmp_path):
        """CONSTRAINT #4: a readonly instance must not silently no-op a
        write."""
        db_url = f"sqlite:///{tmp_path / 'ratings.db'}"
        SymbolRatingStore(db_url=db_url)  # write-mode: creates the schema first
        reader = SymbolRatingStore(db_url=db_url, readonly=True)
        with pytest.raises(Exception):
            reader.record_ratings([_event()])

    def test_readonly_store_degrades_to_empty_on_missing_table(self, tmp_path):
        """No prior write-mode store has ever run -> the
        symbol_rating_events table doesn't exist. A readonly instance must
        degrade to empty results, never crash (CONSTRAINT #6)."""
        db_path = tmp_path / "never_written.db"
        db_path.touch()
        reader = SymbolRatingStore(db_url=f"sqlite:///{db_path}", readonly=True)
        assert reader.get_recent(limit=10) == []
        assert reader.get_consecutive_bad_cycles("AAPL") == 0
        assert reader.get_excluded_symbols(threshold_cycles=5) == set()

    def test_readonly_store_construction_skips_ddl(self, tmp_path, monkeypatch):
        """readonly=True must not call Base.metadata.create_all -- a write a
        read-only engine would reject anyway."""
        import rating.symbol_rating_store as store_module

        calls = []
        monkeypatch.setattr(
            store_module.Base.metadata, "create_all",
            lambda *a, **k: calls.append("create_all"),
        )
        SymbolRatingStore(db_url=f"sqlite:///{tmp_path / 'ratings.db'}", readonly=True)
        assert calls == []


class TestTimestampHandling:
    def test_explicit_timestamp_is_tz_stripped_to_utc(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        ts = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
        store.record_ratings([_event("AAPL", timestamp=ts)])
        rows = store.get_recent(limit=10)
        assert rows[0]["timestamp"].startswith("2026-07-18T12:00:00")

    def test_default_timestamp_is_populated(self):
        store = SymbolRatingStore(db_url="sqlite:///:memory:")
        store.record_ratings([_event("AAPL")])
        rows = store.get_recent(limit=10)
        assert rows[0]["timestamp"] is not None

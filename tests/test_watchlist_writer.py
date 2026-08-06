"""
tests/test_watchlist_writer.py
===============================
Unit tests for pilots/watchlist_writer.py's ``remove_symbols`` and
``record_fetch_failures`` -- the 3-strike permanent-ticker-removal rule
``ml/forecast_backfill.py::step_1_fetch_data`` uses when neither FMP nor the
CompositeProvider fallback returns real data for a ticker.

``append_symbols`` (the pre-existing half of this module) is exercised
indirectly via tests/test_pilots_api.py; these two functions have no such
API-level caller and are covered here directly.
"""
import json

from pilots.watchlist_writer import record_fetch_failures, remove_symbols


# ---------------------------------------------------------------------------
# remove_symbols
# ---------------------------------------------------------------------------

def test_remove_symbols_drops_matching_tickers_case_insensitively(tmp_path):
    wl = tmp_path / "watchlist.txt"
    wl.write_text("AAPL\nmsft\nNVDA\n", encoding="utf-8")

    removed = remove_symbols(["aapl", "NVDA"], path=wl)

    assert removed == ["AAPL", "NVDA"]
    # The untouched line's original casing is preserved as-is -- only the
    # match itself is case-insensitive.
    assert wl.read_text(encoding="utf-8").splitlines() == ["msft"]


def test_remove_symbols_preserves_comments_and_blank_lines(tmp_path):
    wl = tmp_path / "watchlist.txt"
    wl.write_text(
        "# core holdings\nAAPL\n\n# added later\nMSFT\nNVDA\n",
        encoding="utf-8",
    )

    remove_symbols(["MSFT"], path=wl)

    assert wl.read_text(encoding="utf-8").splitlines() == [
        "# core holdings",
        "AAPL",
        "",
        "# added later",
        "NVDA",
    ]


def test_remove_symbols_ignores_symbols_not_present(tmp_path):
    wl = tmp_path / "watchlist.txt"
    original = "AAPL\nMSFT\n"
    wl.write_text(original, encoding="utf-8")

    removed = remove_symbols(["ZZZZ_NOT_REAL"], path=wl)

    assert removed == []
    assert wl.read_text(encoding="utf-8") == original


def test_remove_symbols_missing_file_is_a_noop(tmp_path):
    wl = tmp_path / "does_not_exist.txt"
    assert remove_symbols(["AAPL"], path=wl) == []
    assert not wl.exists()


def test_remove_symbols_empty_list_is_a_noop(tmp_path):
    wl = tmp_path / "watchlist.txt"
    original = "AAPL\n"
    wl.write_text(original, encoding="utf-8")

    assert remove_symbols([], path=wl) == []
    assert wl.read_text(encoding="utf-8") == original


def test_remove_symbols_can_empty_the_file(tmp_path):
    wl = tmp_path / "watchlist.txt"
    wl.write_text("AAPL\n", encoding="utf-8")

    removed = remove_symbols(["AAPL"], path=wl)

    assert removed == ["AAPL"]
    assert wl.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# record_fetch_failures
# ---------------------------------------------------------------------------

def _paths(tmp_path):
    wl = tmp_path / "watchlist.txt"
    failures = tmp_path / "watchlist_failures.json"
    return wl, failures


def test_first_failure_increments_but_does_not_remove(tmp_path):
    wl, failures = _paths(tmp_path)
    wl.write_text("AAPL\nZZZZ\n", encoding="utf-8")

    dropped = record_fetch_failures(["ZZZZ"], watchlist_path=wl, failure_file_path=failures)

    assert dropped == []
    assert "ZZZZ" in wl.read_text(encoding="utf-8")
    assert json.loads(failures.read_text(encoding="utf-8")) == {"ZZZZ": 1}


def test_third_consecutive_failure_permanently_removes_and_resets_counter(tmp_path):
    wl, failures = _paths(tmp_path)
    wl.write_text("AAPL\nZZZZ\n", encoding="utf-8")

    record_fetch_failures(["ZZZZ"], watchlist_path=wl, failure_file_path=failures)
    record_fetch_failures(["ZZZZ"], watchlist_path=wl, failure_file_path=failures)
    dropped = record_fetch_failures(["ZZZZ"], watchlist_path=wl, failure_file_path=failures)

    assert dropped == ["ZZZZ"]
    assert "ZZZZ" not in wl.read_text(encoding="utf-8")
    assert "AAPL" in wl.read_text(encoding="utf-8")
    # Counter is cleared on removal, not left at 3 -- a symbol re-added later
    # (e.g. via append_symbols) starts back at zero strikes.
    assert json.loads(failures.read_text(encoding="utf-8")) == {}


def test_default_max_failures_is_three(tmp_path):
    wl, failures = _paths(tmp_path)
    wl.write_text("ZZZZ\n", encoding="utf-8")

    for _ in range(2):
        assert record_fetch_failures(["ZZZZ"], watchlist_path=wl, failure_file_path=failures) == []
    assert record_fetch_failures(["ZZZZ"], watchlist_path=wl, failure_file_path=failures) == ["ZZZZ"]


def test_custom_max_failures_is_honored(tmp_path):
    wl, failures = _paths(tmp_path)
    wl.write_text("ZZZZ\n", encoding="utf-8")

    assert record_fetch_failures(
        ["ZZZZ"], max_failures=1, watchlist_path=wl, failure_file_path=failures
    ) == ["ZZZZ"]


def test_succeeded_symbols_resets_strike_counter(tmp_path):
    """A ticker that fails, then succeeds, then fails again must restart at
    strike 1 -- not resume from its stale prior count -- so "3 consecutive
    failures" stays honestly consecutive rather than "3 failures ever"."""
    wl, failures = _paths(tmp_path)
    wl.write_text("ZZZZ\n", encoding="utf-8")

    # Two failures -> one strike away from removal.
    record_fetch_failures(["ZZZZ"], watchlist_path=wl, failure_file_path=failures)
    record_fetch_failures(["ZZZZ"], watchlist_path=wl, failure_file_path=failures)
    assert json.loads(failures.read_text(encoding="utf-8")) == {"ZZZZ": 2}

    # A later cycle where ZZZZ succeeds resets it.
    record_fetch_failures([], watchlist_path=wl, failure_file_path=failures, succeeded_symbols=["ZZZZ"])
    assert json.loads(failures.read_text(encoding="utf-8")) == {}

    # Two more failures should NOT be enough to trip removal now.
    record_fetch_failures(["ZZZZ"], watchlist_path=wl, failure_file_path=failures)
    dropped = record_fetch_failures(["ZZZZ"], watchlist_path=wl, failure_file_path=failures)
    assert dropped == []
    assert "ZZZZ" in wl.read_text(encoding="utf-8")


def test_succeeded_symbols_with_no_prior_failures_is_a_noop(tmp_path):
    wl, failures = _paths(tmp_path)
    wl.write_text("AAPL\n", encoding="utf-8")

    dropped = record_fetch_failures([], watchlist_path=wl, failure_file_path=failures, succeeded_symbols=["AAPL"])

    assert dropped == []
    assert not failures.exists()


def test_no_symbols_and_no_successes_is_a_complete_noop(tmp_path):
    wl, failures = _paths(tmp_path)
    assert record_fetch_failures([], watchlist_path=wl, failure_file_path=failures) == []
    assert not failures.exists()


def test_corrupt_json_resets_instead_of_raising(tmp_path):
    wl, failures = _paths(tmp_path)
    wl.write_text("ZZZZ\n", encoding="utf-8")
    failures.write_text("{not valid json", encoding="utf-8")

    dropped = record_fetch_failures(["ZZZZ"], watchlist_path=wl, failure_file_path=failures)

    assert dropped == []
    assert json.loads(failures.read_text(encoding="utf-8")) == {"ZZZZ": 1}


def test_non_object_json_resets_instead_of_raising(tmp_path):
    wl, failures = _paths(tmp_path)
    wl.write_text("ZZZZ\n", encoding="utf-8")
    failures.write_text("[1, 2, 3]", encoding="utf-8")

    dropped = record_fetch_failures(["ZZZZ"], watchlist_path=wl, failure_file_path=failures)

    assert dropped == []
    assert json.loads(failures.read_text(encoding="utf-8")) == {"ZZZZ": 1}


def test_symbols_are_normalized_case_insensitively(tmp_path):
    wl, failures = _paths(tmp_path)
    wl.write_text("zzzz\n", encoding="utf-8")

    record_fetch_failures(["zzzz"], watchlist_path=wl, failure_file_path=failures)
    dropped = record_fetch_failures(["ZZZZ"], watchlist_path=wl, failure_file_path=failures, succeeded_symbols=None)

    assert dropped == []
    assert json.loads(failures.read_text(encoding="utf-8")) == {"ZZZZ": 2}


def test_default_paths_derive_failure_file_next_to_watchlist(tmp_path, monkeypatch):
    """With no explicit failure_file_path, the counter file must live beside
    whatever watchlist_path resolves to (watchlist_failures.json), not a
    hardcoded/CWD-relative location independent of it."""
    wl = tmp_path / "watchlist.txt"
    wl.write_text("ZZZZ\n", encoding="utf-8")

    record_fetch_failures(["ZZZZ"], watchlist_path=wl)

    expected_failures = tmp_path / "watchlist_failures.json"
    assert expected_failures.exists()
    assert json.loads(expected_failures.read_text(encoding="utf-8")) == {"ZZZZ": 1}


def test_multiple_symbols_only_removes_the_ones_that_hit_the_threshold(tmp_path):
    wl, failures = _paths(tmp_path)
    wl.write_text("AAPL\nZZZZ\n", encoding="utf-8")

    record_fetch_failures(["ZZZZ"], watchlist_path=wl, failure_file_path=failures)
    record_fetch_failures(["ZZZZ"], watchlist_path=wl, failure_file_path=failures)
    dropped = record_fetch_failures(["AAPL", "ZZZZ"], watchlist_path=wl, failure_file_path=failures)

    assert dropped == ["ZZZZ"]
    content = wl.read_text(encoding="utf-8")
    assert "AAPL" in content
    assert "ZZZZ" not in content
    assert json.loads(failures.read_text(encoding="utf-8")) == {"AAPL": 1}

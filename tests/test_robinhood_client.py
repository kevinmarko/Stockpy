"""
tests/test_robinhood_client.py
================================
Unit tests for ``data/robinhood_client.py`` — the legacy
holdings+dividends fetcher plus the Task 1.4 automated ticker-discovery
API (``discover_watchlists``/``discover_universe``). This module had no
owning test file at all before this suite (flagged in the original
test-coverage audit and carried forward into Phase 5); it was previously
exercised only indirectly through ``tests/test_portfolio_sync.py``'s
higher-level fakes, which never call into the real ``RobinhoodClient``
class or its module-level discovery helpers.

All network I/O is monkeypatched at the ``r`` (``robin_stocks.robinhood``)
module alias ``data.robinhood_client`` imports — no real Robinhood/network
call is ever made.

Coverage
--------
* ``RobinhoodClient.login``: missing credentials short-circuits without
  calling ``r.login``; a successful login sets ``is_authenticated``; a
  response without ``access_token`` is treated as a failed login; an
  exception from ``r.login`` is caught and degrades to ``False``.
* ``RobinhoodClient.fetch_positions``: unauthenticated short-circuits to
  ``{}``; holdings + matching paid/reinvested dividends merge correctly by
  instrument id; pending/scheduled dividend states are excluded; a
  malformed instrument URL is skipped, not fatal; any exception degrades
  to ``{}`` rather than raising.
* ``RobinhoodClient.list_watchlist_names``: unauthenticated short-circuits
  to ``[]``; both the ``{"results": [...]}`` and bare-list response shapes
  are accepted; non-dict entries and entries without a name are skipped;
  an ``r.get_all_watchlists`` exception degrades to ``[]``.
* ``_suppress_rs_output``: redirects ``robin_stocks``' internal output
  sink for the duration of the block and restores the prior sink
  afterward, even when the block raises; degrades to a harmless
  standalone ``StringIO`` when the ``robin_stocks.robinhood.helper``
  import failed at module load (``_rs_helper is None``).
* ``_sanitize_tickers``: uppercases, strips, dedupes, sorts; drops
  non-string entries and comment-prefixed (``#...``) entries.
* ``_watchlist_tickers``: accepts ``list[{"symbol": ...}]``, a bare list
  of strings, and a ``{"results": [...]}`` wrapper; a per-list exception
  degrades to ``[]`` (dead-letter, never propagates).
* ``discover_watchlists``: unauthenticated short-circuits to ``{}``;
  aggregates ``{name: [tickers]}`` across every watchlist name; a single
  bad watchlist doesn't drop the others (per-list isolation, since
  ``_watchlist_tickers`` degrades that watchlist to ``[]`` individually).
* ``_file_tickers``: missing file returns ``[]``; comment lines and blank
  lines are dropped; a read exception (e.g. permissions) degrades to
  ``[]``.
* ``_watchlist_files_from_env``: parses the colon-separated
  ``SYNC_WATCHLIST_FILES`` env var; empty/unset env returns ``[]``.
* ``discover_universe``: unions holdings ∪ every watchlist ∪ file-backed
  tickers (both ``extra_files`` argument and the env var) into one sorted,
  deduped list; unauthenticated client contributes no holdings but file
  sources still work; a holdings-fetch exception doesn't abort the other
  sources (dead-letter per source, not per call).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

import data.robinhood_client as robinhood_client
from data.robinhood_client import (
    RobinhoodClient,
    _file_tickers,
    _sanitize_tickers,
    _suppress_rs_output,
    _watchlist_files_from_env,
    _watchlist_tickers,
    discover_universe,
    discover_watchlists,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_client(monkeypatch, *, authenticated: bool = True) -> RobinhoodClient:
    """Build a RobinhoodClient without touching real settings/.env, with
    ``is_authenticated`` pre-set so tests can skip the login flow."""
    monkeypatch.setattr(robinhood_client.settings, "ROBINHOOD_USERNAME", "user@example.com")
    monkeypatch.setattr(robinhood_client.settings, "ROBINHOOD_PASSWORD", "hunter2")
    client = RobinhoodClient()
    client.is_authenticated = authenticated
    return client


class _FakeRHelper:
    """Minimal stand-in for ``robin_stocks.robinhood.helper`` exposing only
    the ``get_output``/``set_output`` pair ``_suppress_rs_output`` uses."""

    def __init__(self) -> None:
        self._sink = "SENTINEL_DEFAULT_SINK"

    def get_output(self):
        return self._sink

    def set_output(self, sink) -> None:
        self._sink = sink


# ---------------------------------------------------------------------------
# RobinhoodClient.login
# ---------------------------------------------------------------------------


class TestLogin:
    def test_missing_credentials_short_circuits(self, monkeypatch):
        monkeypatch.setattr(robinhood_client.settings, "ROBINHOOD_USERNAME", None)
        monkeypatch.setattr(robinhood_client.settings, "ROBINHOOD_PASSWORD", None)
        called = []
        monkeypatch.setattr(robinhood_client.r, "login", lambda *a, **k: called.append(1))

        client = RobinhoodClient()
        ok = client.login()

        assert ok is False
        assert client.is_authenticated is False
        assert called == []

    def test_successful_login_sets_authenticated(self, monkeypatch):
        monkeypatch.setattr(robinhood_client.settings, "ROBINHOOD_USERNAME", "u@example.com")
        monkeypatch.setattr(robinhood_client.settings, "ROBINHOOD_PASSWORD", "pw")
        monkeypatch.setattr(
            robinhood_client.r, "login", lambda u, p: {"access_token": "tok123"}
        )

        client = RobinhoodClient()
        ok = client.login()

        assert ok is True
        assert client.is_authenticated is True

    def test_response_without_access_token_is_a_failed_login(self, monkeypatch):
        monkeypatch.setattr(robinhood_client.settings, "ROBINHOOD_USERNAME", "u@example.com")
        monkeypatch.setattr(robinhood_client.settings, "ROBINHOOD_PASSWORD", "pw")
        monkeypatch.setattr(robinhood_client.r, "login", lambda u, p: {"detail": "mfa_required"})

        client = RobinhoodClient()
        ok = client.login()

        assert ok is False
        assert client.is_authenticated is False

    def test_login_exception_degrades_to_false(self, monkeypatch):
        monkeypatch.setattr(robinhood_client.settings, "ROBINHOOD_USERNAME", "u@example.com")
        monkeypatch.setattr(robinhood_client.settings, "ROBINHOOD_PASSWORD", "pw")

        def _raise(u, p):
            raise ConnectionError("network down")

        monkeypatch.setattr(robinhood_client.r, "login", _raise)

        client = RobinhoodClient()
        ok = client.login()

        assert ok is False
        assert client.is_authenticated is False


# ---------------------------------------------------------------------------
# RobinhoodClient.fetch_positions
# ---------------------------------------------------------------------------


class TestFetchPositions:
    def test_unauthenticated_short_circuits_to_empty(self, monkeypatch):
        client = _make_client(monkeypatch, authenticated=False)
        assert client.fetch_positions() == {}

    def test_holdings_and_dividends_merge_by_instrument_id(self, monkeypatch):
        client = _make_client(monkeypatch)
        monkeypatch.setattr(
            robinhood_client.r,
            "build_holdings",
            lambda: {
                "AAPL": {"quantity": "10", "average_buy_price": "150.0", "id": "inst-aapl"},
                "MSFT": {"quantity": "5", "average_buy_price": "300.0", "id": "inst-msft"},
            },
        )
        monkeypatch.setattr(
            robinhood_client.r,
            "get_dividends",
            lambda: [
                {"instrument": "https://api.robinhood.com/instruments/inst-aapl/", "amount": "1.50", "state": "paid"},
                {"instrument": "https://api.robinhood.com/instruments/inst-aapl/", "amount": "1.50", "state": "reinvested"},
                {"instrument": "https://api.robinhood.com/instruments/inst-msft/", "amount": "99.0", "state": "pending"},
            ],
        )

        positions = client.fetch_positions()

        assert set(positions.keys()) == {"AAPL", "MSFT"}
        assert positions["AAPL"].shares == 10.0
        assert positions["AAPL"].average_cost == 150.0
        assert positions["AAPL"].total_dividends == pytest.approx(3.0)
        # MSFT's only dividend record is "pending" -> excluded.
        assert positions["MSFT"].total_dividends == 0.0

    def test_malformed_instrument_url_is_skipped_not_fatal(self, monkeypatch):
        client = _make_client(monkeypatch)
        monkeypatch.setattr(
            robinhood_client.r,
            "build_holdings",
            lambda: {"AAPL": {"quantity": "1", "average_buy_price": "1.0", "id": "inst-aapl"}},
        )
        monkeypatch.setattr(
            robinhood_client.r,
            "get_dividends",
            lambda: [{"instrument": None, "amount": "1.0", "state": "paid"}],
        )

        positions = client.fetch_positions()

        assert positions["AAPL"].total_dividends == 0.0

    def test_exception_degrades_to_empty_dict(self, monkeypatch):
        client = _make_client(monkeypatch)

        def _raise():
            raise RuntimeError("robinhood API down")

        monkeypatch.setattr(robinhood_client.r, "build_holdings", _raise)

        assert client.fetch_positions() == {}


# ---------------------------------------------------------------------------
# RobinhoodClient.list_watchlist_names
# ---------------------------------------------------------------------------


class TestListWatchlistNames:
    def test_unauthenticated_short_circuits_to_empty(self, monkeypatch):
        client = _make_client(monkeypatch, authenticated=False)
        assert client.list_watchlist_names() == []

    def test_results_wrapper_shape(self, monkeypatch):
        client = _make_client(monkeypatch)
        monkeypatch.setattr(
            robinhood_client.r,
            "get_all_watchlists",
            lambda: {"results": [{"display_name": "Tech"}, {"name": "Dividends"}]},
        )

        assert client.list_watchlist_names() == ["Tech", "Dividends"]

    def test_bare_list_shape(self, monkeypatch):
        client = _make_client(monkeypatch)
        monkeypatch.setattr(
            robinhood_client.r,
            "get_all_watchlists",
            lambda: [{"display_name": "Solo List"}],
        )

        assert client.list_watchlist_names() == ["Solo List"]

    def test_non_dict_and_unnamed_entries_are_skipped(self, monkeypatch):
        client = _make_client(monkeypatch)
        monkeypatch.setattr(
            robinhood_client.r,
            "get_all_watchlists",
            lambda: {"results": ["not-a-dict", {}, {"display_name": "Kept"}]},
        )

        assert client.list_watchlist_names() == ["Kept"]

    def test_unexpected_shape_returns_empty(self, monkeypatch):
        client = _make_client(monkeypatch)
        monkeypatch.setattr(robinhood_client.r, "get_all_watchlists", lambda: {"results": "not-a-list"})

        assert client.list_watchlist_names() == []

    def test_exception_degrades_to_empty(self, monkeypatch):
        client = _make_client(monkeypatch)

        def _raise():
            raise RuntimeError("API down")

        monkeypatch.setattr(robinhood_client.r, "get_all_watchlists", _raise)

        assert client.list_watchlist_names() == []


# ---------------------------------------------------------------------------
# _suppress_rs_output
# ---------------------------------------------------------------------------


class TestSuppressRsOutput:
    def test_redirects_and_restores_output_sink(self, monkeypatch):
        fake_helper = _FakeRHelper()
        monkeypatch.setattr(robinhood_client, "_rs_helper", fake_helper)

        with _suppress_rs_output() as buf:
            print("captured noise", file=fake_helper.get_output())
            assert fake_helper.get_output() is buf

        assert fake_helper.get_output() == "SENTINEL_DEFAULT_SINK"
        assert "captured noise" in buf.getvalue()

    def test_restores_sink_even_when_block_raises(self, monkeypatch):
        fake_helper = _FakeRHelper()
        monkeypatch.setattr(robinhood_client, "_rs_helper", fake_helper)

        with pytest.raises(ValueError):
            with _suppress_rs_output():
                raise ValueError("boom")

        assert fake_helper.get_output() == "SENTINEL_DEFAULT_SINK"

    def test_none_helper_yields_harmless_stringio(self, monkeypatch):
        monkeypatch.setattr(robinhood_client, "_rs_helper", None)

        with _suppress_rs_output() as buf:
            buf.write("fine, goes nowhere")

        # No exception, and nothing external was touched.
        assert buf.getvalue() == "fine, goes nowhere"


# ---------------------------------------------------------------------------
# _sanitize_tickers
# ---------------------------------------------------------------------------


class TestSanitizeTickers:
    def test_uppercases_strips_dedupes_sorts(self):
        assert _sanitize_tickers([" aapl ", "MSFT", "aapl"]) == ["AAPL", "MSFT"]

    def test_non_string_entries_dropped(self):
        assert _sanitize_tickers(["AAPL", 123, None, {"x": 1}]) == ["AAPL"]

    def test_comment_prefixed_entries_dropped(self):
        assert _sanitize_tickers(["AAPL", "# a comment", ""]) == ["AAPL"]


# ---------------------------------------------------------------------------
# _watchlist_tickers
# ---------------------------------------------------------------------------


class TestWatchlistTickers:
    def test_list_of_dicts_with_symbol_key(self, monkeypatch):
        monkeypatch.setattr(
            robinhood_client.r,
            "get_watchlist_by_name",
            lambda name: [{"symbol": "aapl"}, {"symbol": "msft"}],
        )
        assert _watchlist_tickers("Tech") == ["AAPL", "MSFT"]

    def test_bare_list_of_strings(self, monkeypatch):
        monkeypatch.setattr(
            robinhood_client.r, "get_watchlist_by_name", lambda name: ["aapl", "msft"]
        )
        assert _watchlist_tickers("Tech") == ["AAPL", "MSFT"]

    def test_results_wrapper_shape(self, monkeypatch):
        monkeypatch.setattr(
            robinhood_client.r,
            "get_watchlist_by_name",
            lambda name: {"results": [{"symbol": "aapl"}]},
        )
        assert _watchlist_tickers("Tech") == ["AAPL"]

    def test_exception_degrades_to_empty(self, monkeypatch):
        def _raise(name):
            raise RuntimeError("400 for system list")

        monkeypatch.setattr(robinhood_client.r, "get_watchlist_by_name", _raise)

        assert _watchlist_tickers("100 Most Popular") == []

    def test_unexpected_shape_returns_empty(self, monkeypatch):
        monkeypatch.setattr(robinhood_client.r, "get_watchlist_by_name", lambda name: 42)
        assert _watchlist_tickers("Weird") == []


# ---------------------------------------------------------------------------
# discover_watchlists
# ---------------------------------------------------------------------------


class TestDiscoverWatchlists:
    def test_unauthenticated_short_circuits_to_empty(self, monkeypatch):
        client = _make_client(monkeypatch, authenticated=False)
        assert discover_watchlists(client) == {}

    def test_aggregates_across_all_watchlists(self, monkeypatch):
        client = _make_client(monkeypatch)
        monkeypatch.setattr(
            robinhood_client.r,
            "get_all_watchlists",
            lambda: {"results": [{"display_name": "Tech"}, {"display_name": "Dividends"}]},
        )

        def _by_name(name):
            return {
                "Tech": [{"symbol": "AAPL"}],
                "Dividends": [{"symbol": "KO"}, {"symbol": "PG"}],
            }[name]

        monkeypatch.setattr(robinhood_client.r, "get_watchlist_by_name", _by_name)

        assert discover_watchlists(client) == {
            "Tech": ["AAPL"],
            "Dividends": ["KO", "PG"],
        }

    def test_one_bad_watchlist_does_not_drop_the_others(self, monkeypatch):
        client = _make_client(monkeypatch)
        monkeypatch.setattr(
            robinhood_client.r,
            "get_all_watchlists",
            lambda: {"results": [{"display_name": "Good"}, {"display_name": "100 Most Popular"}]},
        )

        def _by_name(name):
            if name == "100 Most Popular":
                raise RuntimeError("400 Bad Request")
            return [{"symbol": "AAPL"}]

        monkeypatch.setattr(robinhood_client.r, "get_watchlist_by_name", _by_name)

        result = discover_watchlists(client)

        assert result["Good"] == ["AAPL"]
        assert result["100 Most Popular"] == []


# ---------------------------------------------------------------------------
# _file_tickers / _watchlist_files_from_env
# ---------------------------------------------------------------------------


class TestFileTickers:
    def test_missing_file_returns_empty(self, tmp_path):
        assert _file_tickers(tmp_path / "does_not_exist.txt") == []

    def test_reads_and_sanitizes_lines(self, tmp_path):
        f = tmp_path / "watchlist.txt"
        f.write_text("aapl\n# a comment\n\nmsft\n", encoding="utf-8")

        assert _file_tickers(f) == ["AAPL", "MSFT"]

    def test_read_exception_degrades_to_empty(self, monkeypatch, tmp_path):
        f = tmp_path / "watchlist.txt"
        f.write_text("aapl\n", encoding="utf-8")

        def _raise(*a, **k):
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "read_text", _raise)

        assert _file_tickers(f) == []


class TestWatchlistFilesFromEnv:
    def test_unset_env_returns_empty(self, monkeypatch):
        monkeypatch.delenv("SYNC_WATCHLIST_FILES", raising=False)
        assert _watchlist_files_from_env() == []

    def test_parses_colon_separated_paths(self, monkeypatch):
        monkeypatch.setenv("SYNC_WATCHLIST_FILES", "/a/b.txt: /c/d.txt :")
        paths = _watchlist_files_from_env()
        assert [str(p) for p in paths] == ["/a/b.txt", "/c/d.txt"]


# ---------------------------------------------------------------------------
# discover_universe
# ---------------------------------------------------------------------------


class TestDiscoverUniverse:
    def test_unions_holdings_watchlists_and_files(self, monkeypatch, tmp_path):
        client = _make_client(monkeypatch)
        monkeypatch.setattr(
            robinhood_client.r,
            "build_holdings",
            lambda: {"AAPL": {"quantity": "1", "average_buy_price": "1.0", "id": "i1"}},
        )
        monkeypatch.setattr(robinhood_client.r, "get_dividends", lambda: [])
        monkeypatch.setattr(
            robinhood_client.r,
            "get_all_watchlists",
            lambda: {"results": [{"display_name": "Tech"}]},
        )
        monkeypatch.setattr(
            robinhood_client.r, "get_watchlist_by_name", lambda name: [{"symbol": "MSFT"}]
        )
        f = tmp_path / "extra.txt"
        f.write_text("ko\n", encoding="utf-8")

        universe = discover_universe(client, extra_files=[f])

        assert universe == ["AAPL", "KO", "MSFT"]

    def test_unauthenticated_client_still_uses_file_sources(self, monkeypatch, tmp_path):
        client = _make_client(monkeypatch, authenticated=False)
        f = tmp_path / "extra.txt"
        f.write_text("pg\n", encoding="utf-8")

        universe = discover_universe(client, extra_files=[f])

        assert universe == ["PG"]

    def test_holdings_fetch_exception_does_not_abort_other_sources(self, monkeypatch, tmp_path):
        client = _make_client(monkeypatch)

        def _raise():
            raise RuntimeError("holdings API down")

        monkeypatch.setattr(robinhood_client.r, "build_holdings", _raise)
        monkeypatch.setattr(robinhood_client.r, "get_all_watchlists", lambda: {"results": []})
        f = tmp_path / "extra.txt"
        f.write_text("pg\n", encoding="utf-8")

        universe = discover_universe(client, extra_files=[f])

        assert universe == ["PG"]

    def test_env_var_files_are_included_alongside_extra_files_argument(self, monkeypatch, tmp_path):
        client = _make_client(monkeypatch, authenticated=False)
        env_file = tmp_path / "env_list.txt"
        env_file.write_text("aapl\n", encoding="utf-8")
        arg_file = tmp_path / "arg_list.txt"
        arg_file.write_text("msft\n", encoding="utf-8")
        monkeypatch.setenv("SYNC_WATCHLIST_FILES", str(env_file))

        universe = discover_universe(client, extra_files=[arg_file])

        assert universe == ["AAPL", "MSFT"]

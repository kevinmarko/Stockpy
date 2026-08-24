"""
tests/test_portfolio_sync.py
============================
Offline tests for Task 1.4 — Portfolio & Watchlist Synchronization Engine
(``data/portfolio_sync.py`` + the discovery helpers in
``data/robinhood_client.py``).

All Robinhood and market-data calls are monkey-patched.  The tests cover:

* **happy path**         — held + watchlist + file inputs produce one deduped,
                             fully-classified ``SyncReport``;
* **coverage gaps**      — a held symbol with no quote/bar is classified
                             ``EQUITY_ONLY`` (NOT silently dropped) and its
                             P&L derivation uses Robinhood's cost basis;
* **dedup + sort**       — overlapping sources collapse into one alphabetised
                             universe;
* **async dry-run**      — ``async_sync_now(persist_default_tickers=False)``
                             skips the ``.env`` write side-effect;
* **env_io guard**       — a permission failure inside the persist step is
                             swallowed and never propagates (CONSTRAINT #6).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Test doubles — minimal stand-ins for AccountSnapshot, RobinhoodClient,
# CompositeProvider, and the gui.env_io.write_setting side-effect.
# ---------------------------------------------------------------------------


@dataclass
class _FakePosition:
    """Mimics ``data.robinhood_portfolio.PortfolioPosition`` for tests."""

    symbol: str
    quantity: float
    average_cost: float
    current_price: float
    market_value: float
    unrealized_pl: float = 0.0


@dataclass
class _FakeSnapshot:
    """Mimics ``data.robinhood_portfolio.AccountSnapshot``."""

    positions: dict[str, _FakePosition]
    buying_power: float = 1_000.0
    total_equity: float = 10_000.0
    total_dividends: float = 0.0
    fetched_at: datetime = datetime.now(timezone.utc)


class _FakeRobinhoodClient:
    """Has the same surface as ``data.robinhood_client.RobinhoodClient`` —
    only the bits :mod:`data.portfolio_sync` actually touches.
    """

    def __init__(self, holdings: dict[str, Any], watchlists: dict[str, list]):
        self._holdings = holdings
        self._watchlists = watchlists
        self.is_authenticated = True

    def fetch_positions(self):
        return self._holdings

    # Methods used by data.robinhood_client.discover_watchlists():
    def list_watchlist_names(self):
        return list(self._watchlists.keys())


@dataclass
class _FakeQuote:
    symbol: str
    price: float
    is_stale: bool
    source: str = "alpaca"


class _FakeProvider:
    """Mimics ``data.market_data.CompositeProvider`` for the sync probe."""

    quote_source = "alpaca"

    def __init__(
        self,
        *,
        covered: set | None = None,
        has_funds: set | None = None,
        stale: set | None = None,
    ):
        self._covered = covered or set()
        self._has_funds = has_funds or set()
        self._stale = stale or set()

    def get_latest_quote(self, symbol: str) -> _FakeQuote:
        if symbol not in self._covered:
            raise RuntimeError(f"no quote for {symbol}")
        return _FakeQuote(
            symbol=symbol, price=100.0 + len(symbol), is_stale=symbol in self._stale,
        )

    def get_intraday_bars(self, symbol: str, lookback_days: int = 5):
        if symbol not in self._covered:
            raise RuntimeError(f"no bars for {symbol}")
        idx = pd.date_range("2025-01-01", periods=lookback_days)
        return pd.DataFrame(
            {
                "Open": 100.0, "High": 101.0, "Low": 99.0,
                "Close": 100.5, "Volume": 1_000,
            },
            index=idx,
        )

    def get_fundamentals(self, symbol: str) -> dict[str, Any]:
        if symbol in self._has_funds:
            return {"trailingPE": 12.0, "marketCap": 5e9}
        return {}


# ---------------------------------------------------------------------------
# Module-under-test imports happen INSIDE each test so the monkeypatching
# fixtures see fresh module state.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_market_provider(monkeypatch):
    """Each test gets its own provider — avoid singleton bleed across tests."""

    import data.market_data as md

    monkeypatch.setattr(md, "_default_provider", None, raising=False)
    yield


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_build_sync_report_happy_path(monkeypatch):
    """Held + watchlist + file inputs produce one deduped, classified report."""
    from data import portfolio_sync as ps
    from data import robinhood_client as rc
    from settings import settings

    # This test's expected universe is a closed, hand-enumerated set (holdings
    # + the one fake RH watchlist below) -- it must NOT also pick up whatever
    # real file(s) an operator's own .env happens to point SYNC_WATCHLIST_FILES
    # at (e.g. a real, populated `watchlist.txt` sitting at the repo root when
    # pytest is invoked from there). Neither a bool nor a `gui.env_io.
    # SECRET_KEYS` member, so conftest.py's blanket per-test reset does not
    # cover it -- pin it explicitly, matching this file's other
    # SYNC_WATCHLIST_FILES-aware tests below.
    monkeypatch.setattr(settings, "SYNC_WATCHLIST_FILES", None)

    held = {
        "AAPL": _FakePosition("AAPL", 10, 150.0, 175.0, 1_750.0),
        "MSFT": _FakePosition("MSFT", 5, 300.0, 320.0, 1_600.0),
    }
    snap = _FakeSnapshot(positions=held)

    client = _FakeRobinhoodClient(
        holdings=held,
        watchlists={"Mega Tech": ["NVDA", "GOOG", "AAPL"]},  # AAPL overlaps holdings
    )

    # Mock the per-list ticker fetch used inside discover_watchlists():
    def _fake_watchlist_tickers(name: str):
        return client._watchlists.get(name, [])

    monkeypatch.setattr(rc, "_watchlist_tickers", _fake_watchlist_tickers)

    # Force build_sync_report to use our fake provider.
    provider = _FakeProvider(
        covered={"AAPL", "MSFT", "NVDA", "GOOG"},
        has_funds={"AAPL", "MSFT", "NVDA"},
    )
    monkeypatch.setattr(ps, "get_provider", lambda: provider, raising=False)
    import data.market_data as md

    monkeypatch.setattr(md, "get_provider", lambda: provider)

    report = ps.build_sync_report(snap, client=client)

    # Universe = holdings ∪ watchlist, deduped.
    assert sorted(report.symbols.keys()) == ["AAPL", "GOOG", "MSFT", "NVDA"]

    # AAPL: held + full coverage + on a watchlist.
    aapl = report.symbols["AAPL"]
    assert aapl.held is True
    assert aapl.coverage is ps.CoverageStatus.FULL
    assert aapl.watchlists == ("Mega Tech",)
    # Delta uses live quote (100 + len("AAPL")=4 → 104) − avg cost 150 = -46
    assert aapl.cost_basis_delta_per_share == pytest.approx(104.0 - 150.0)

    # GOOG: not held, quoted but no fundamentals → QUOTES_ONLY.
    goog = report.symbols["GOOG"]
    assert goog.held is False
    assert goog.coverage is ps.CoverageStatus.QUOTES_ONLY

    # n_full counts only full-coverage rows.
    assert report.n_full == 3  # AAPL, MSFT, NVDA
    assert report.n_equity_only == 0
    assert report.n_uncovered == 0


# ---------------------------------------------------------------------------
# Stale quote coverage — otherwise-FULL symbol with a stale quote → STALE
# ---------------------------------------------------------------------------


def test_stale_quote_produces_stale_status_not_full(monkeypatch):
    """A symbol with quote + bars + fundamentals all reachable, but whose
    quote is flagged ``is_stale=True`` (delayed feed / past the provider's
    staleness threshold), must be classified STALE -- distinct from FULL --
    rather than being silently folded into FULL."""
    import data.market_data as md
    from data import portfolio_sync as ps
    from data import robinhood_client as rc

    held = {"AAPL": _FakePosition("AAPL", 10, 150.0, 175.0, 1_750.0)}
    snap = _FakeSnapshot(positions=held)

    client = _FakeRobinhoodClient(holdings=held, watchlists={})
    monkeypatch.setattr(rc, "_watchlist_tickers", lambda name: [])

    provider = _FakeProvider(
        covered={"AAPL"}, has_funds={"AAPL"}, stale={"AAPL"},
    )
    monkeypatch.setattr(md, "get_provider", lambda: provider)

    report = ps.build_sync_report(snap, client=client)

    aapl = report.symbols["AAPL"]
    assert aapl.is_stale_quote is True
    assert aapl.coverage is ps.CoverageStatus.STALE
    assert aapl.coverage is not ps.CoverageStatus.FULL
    assert report.n_stale == 1
    assert report.n_full == 0


def test_fresh_quote_still_classifies_full(monkeypatch):
    """Sanity check: a fresh quote with full coverage still produces FULL,
    not STALE -- the new status must be additive, not a regression on the
    existing happy path."""
    import data.market_data as md
    from data import portfolio_sync as ps
    from data import robinhood_client as rc

    held = {"AAPL": _FakePosition("AAPL", 10, 150.0, 175.0, 1_750.0)}
    snap = _FakeSnapshot(positions=held)

    client = _FakeRobinhoodClient(holdings=held, watchlists={})
    monkeypatch.setattr(rc, "_watchlist_tickers", lambda name: [])

    provider = _FakeProvider(covered={"AAPL"}, has_funds={"AAPL"}, stale=set())
    monkeypatch.setattr(md, "get_provider", lambda: provider)

    report = ps.build_sync_report(snap, client=client)

    aapl = report.symbols["AAPL"]
    assert aapl.is_stale_quote is False
    assert aapl.coverage is ps.CoverageStatus.FULL
    assert report.n_stale == 0
    assert report.n_full == 1


# ---------------------------------------------------------------------------
# Coverage gaps — held but uncovered  →  EQUITY_ONLY (never dropped)
# ---------------------------------------------------------------------------


def test_held_uncovered_classified_equity_only(monkeypatch):
    """A held symbol with no market-data coverage must surface as EQUITY_ONLY."""
    import data.market_data as md
    from data import portfolio_sync as ps
    from data import robinhood_client as rc

    held = {
        "OBSCURE": _FakePosition("OBSCURE", 100, 5.0, 0.0, 0.0),
    }
    snap = _FakeSnapshot(positions=held)

    client = _FakeRobinhoodClient(holdings=held, watchlists={})
    monkeypatch.setattr(rc, "_watchlist_tickers", lambda name: [])

    provider = _FakeProvider(covered=set(), has_funds=set())
    monkeypatch.setattr(md, "get_provider", lambda: provider)

    report = ps.build_sync_report(snap, client=client)

    sym = report.symbols["OBSCURE"]
    assert sym.held is True
    # Upgraded from UNCOVERED → EQUITY_ONLY because it's held.
    assert sym.coverage is ps.CoverageStatus.EQUITY_ONLY
    # No fabricated price.
    assert sym.current_price != sym.current_price  # NaN check
    assert sym.cost_basis_delta_per_share != sym.cost_basis_delta_per_share  # NaN
    # Equity view falls back to qty * avg_cost for total-equity rollup.
    assert report.held_total_equity() == pytest.approx(100 * 5.0)
    assert sym.diagnostic, "diagnostic must record which leg failed"


# ---------------------------------------------------------------------------
# Dedup + sort
# ---------------------------------------------------------------------------


def test_universe_dedup_and_sort(monkeypatch):
    """Overlapping sources collapse to one alphabetised universe."""
    import data.market_data as md
    from data import portfolio_sync as ps
    from data import robinhood_client as rc
    from settings import settings

    # See test_build_sync_report_happy_path's identical comment above --
    # a real operator .env's SYNC_WATCHLIST_FILES must not leak extra
    # tickers into this test's hand-enumerated expected universe.
    monkeypatch.setattr(settings, "SYNC_WATCHLIST_FILES", None)

    held = {"AAPL": _FakePosition("AAPL", 1, 100.0, 100.0, 100.0)}
    snap = _FakeSnapshot(positions=held)

    client = _FakeRobinhoodClient(
        holdings=held,
        watchlists={
            "List A": ["MSFT", "AAPL"],
            "List B": ["AAPL", "NVDA"],
        },
    )

    def _fake_watchlist_tickers(name: str):
        return client._watchlists.get(name, [])

    monkeypatch.setattr(rc, "_watchlist_tickers", _fake_watchlist_tickers)
    monkeypatch.setattr(md, "get_provider", lambda: _FakeProvider(
        covered={"AAPL", "MSFT", "NVDA"}, has_funds=set()
    ))

    report = ps.build_sync_report(snap, client=client)
    assert sorted(report.symbols.keys()) == ["AAPL", "MSFT", "NVDA"]
    # AAPL appears in both watchlists.
    assert set(report.symbols["AAPL"].watchlists) == {"List A", "List B"}


# ---------------------------------------------------------------------------
# Async sync — dry-run skips the .env write
# ---------------------------------------------------------------------------


def test_async_sync_now_dry_run_skips_env_write(monkeypatch, tmp_path):
    """``persist_default_tickers=False`` must NOT touch gui.env_io."""
    import data.market_data as md
    from data import portfolio_sync as ps
    from gui import env_io
    from settings import settings

    # See test_build_sync_report_happy_path's comment above -- pin so a real
    # operator .env's SYNC_WATCHLIST_FILES can't inflate n_total past the
    # single held AAPL position this test expects.
    monkeypatch.setattr(settings, "SYNC_WATCHLIST_FILES", None)

    snap = _FakeSnapshot(positions={
        "AAPL": _FakePosition("AAPL", 1, 100.0, 100.0, 100.0),
    })

    monkeypatch.setattr(md, "get_provider", lambda: _FakeProvider(
        covered={"AAPL"}, has_funds={"AAPL"}
    ))
    # Redirect the cache path so the test doesn't pollute repo state.
    monkeypatch.setattr(ps, "_CACHE_PATH", tmp_path / "sync_report.json")

    write_calls: list = []

    def _spy_write(key, value):
        write_calls.append((key, value))
        return ""

    monkeypatch.setattr(env_io, "write_setting", _spy_write)

    report = asyncio.run(ps.async_sync_now(
        snap, client=None, persist_default_tickers=False,
    ))

    assert report.n_total == 1
    # No env write at all — even DEFAULT_TICKERS must be skipped.
    assert write_calls == []
    # Cache WAS written (refreshes the GUI panel without env IO).
    assert (tmp_path / "sync_report.json").exists()


def test_async_sync_now_persist_swallows_env_io_failure(monkeypatch, tmp_path):
    """A failing env_io.write_setting must NOT propagate (CONSTRAINT #6)."""
    import data.market_data as md
    from data import portfolio_sync as ps
    from gui import env_io
    from settings import settings

    # See test_build_sync_report_happy_path's comment above.
    monkeypatch.setattr(settings, "SYNC_WATCHLIST_FILES", None)

    snap = _FakeSnapshot(positions={
        "AAPL": _FakePosition("AAPL", 1, 100.0, 100.0, 100.0),
    })
    monkeypatch.setattr(md, "get_provider", lambda: _FakeProvider(
        covered={"AAPL"}, has_funds={"AAPL"}
    ))
    monkeypatch.setattr(ps, "_CACHE_PATH", tmp_path / "sync_report.json")

    def _boom(key, value):
        raise env_io.SecretWriteError(f"refused {key}")

    monkeypatch.setattr(env_io, "write_setting", _boom)

    # Must not raise — the GUI refresh handler must complete.
    report = asyncio.run(ps.async_sync_now(snap, client=None))
    assert report.n_total == 1
    # Cache still persisted.
    assert (tmp_path / "sync_report.json").exists()


# ---------------------------------------------------------------------------
# Discovery helpers in robinhood_client
# ---------------------------------------------------------------------------


def test_sanitize_and_file_tickers(tmp_path):
    """File-backed watchlists honour the '# comment' + dedupe convention."""
    from data import robinhood_client as rc

    wl = tmp_path / "wl.txt"
    wl.write_text(
        "AAPL\n"
        "msft\n"
        "# this is a comment\n"
        "\n"
        "AAPL\n"  # duplicate
        "  goog  \n"
    )
    out = rc._file_tickers(wl)
    assert out == ["AAPL", "GOOG", "MSFT"]


def test_discover_universe_dedupes_across_sources(monkeypatch, tmp_path):
    """``discover_universe`` returns one sorted universe across all sources."""
    from data import robinhood_client as rc
    from dto_models import RobinhoodPositionDTO

    client = _FakeRobinhoodClient(
        holdings={"AAPL": RobinhoodPositionDTO("AAPL", 1, 100.0, 0.0)},
        watchlists={"L1": ["MSFT", "AAPL"], "L2": ["NVDA"]},
    )

    # Monkey-patch the per-list reader and the holdings call:
    monkeypatch.setattr(rc, "_watchlist_tickers",
                        lambda name: client._watchlists.get(name, []))

    # Inject a file-backed list via settings.SYNC_WATCHLIST_FILES (read via
    # the `settings` singleton, not os.environ, since 2026-08).
    wl_file = tmp_path / "extra.txt"
    wl_file.write_text("TSLA\nAAPL\n")  # AAPL duplicates again
    monkeypatch.setattr(rc.settings, "SYNC_WATCHLIST_FILES", str(wl_file))

    out = rc.discover_universe(client)
    assert out == ["AAPL", "MSFT", "NVDA", "TSLA"]


def test_unauthenticated_client_returns_empty(monkeypatch):
    """Discovery against a non-authenticated client returns empty containers."""
    from data import robinhood_client as rc

    # See test_build_sync_report_happy_path (tests/test_portfolio_sync.py)'s
    # comment for the full rationale -- an unauthenticated client must
    # discover a genuinely empty universe, not whatever real file an
    # operator's own .env happens to point SYNC_WATCHLIST_FILES at.
    monkeypatch.setattr(rc.settings, "SYNC_WATCHLIST_FILES", None)

    client = rc.RobinhoodClient()
    client.is_authenticated = False
    assert rc.discover_watchlists(client) == {}
    assert rc.discover_universe(client) == []


# ---------------------------------------------------------------------------
# resolve_universe — the "all" sentinel shared by the EDGAR backfill CLI/MCP
# ---------------------------------------------------------------------------


class TestResolveUniverse:
    def test_explicit_list_never_touches_robinhood_or_market(self, monkeypatch):
        """An explicit comma list is sanitized (upper/strip/dedupe/sort) with NO
        snapshot fetch, NO market probe, NO DB — the hot/common path stays free."""
        import data.robinhood_portfolio as rp
        from data import portfolio_sync as ps

        def _boom(*a, **k):  # pragma: no cover - must never run
            raise AssertionError("explicit list must not fetch an account snapshot")

        monkeypatch.setattr(rp, "fetch_account_snapshot", _boom)
        assert ps.resolve_universe("msft, aapl ,AAPL,goog") == ["AAPL", "GOOG", "MSFT"]

    def test_all_degrades_to_default_tickers_when_no_snapshot(self, monkeypatch):
        """The cron/headless degrade path: a failed snapshot fetch still yields
        DEFAULT_TICKERS (CONSTRAINT #6) — never a crash, never empty by accident."""
        import data.robinhood_portfolio as rp
        from data import portfolio_sync as ps
        from settings import settings

        monkeypatch.setattr(
            rp, "fetch_account_snapshot",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no creds")),
        )
        # No file-backed watchlists in play. Read via the `settings`
        # singleton, not os.environ (fixed 2026-08).
        monkeypatch.setattr(settings, "SYNC_WATCHLIST_FILES", None)
        got = ps.resolve_universe("all")
        assert got == sorted({t.upper() for t in settings.DEFAULT_TICKERS})

    def test_all_unions_holdings_watchlist_files_and_defaults(self, monkeypatch, tmp_path):
        """'all' = held ∪ watchlist files ∪ DEFAULT_TICKERS, via a probe_market=False
        build_sync_report (zero per-symbol market I/O)."""
        import data.robinhood_portfolio as rp
        from data import portfolio_sync as ps
        from settings import settings

        # A held position and a file-backed watchlist entry.
        held = {"NVDA": _FakePosition("NVDA", 3, 100.0, 300.0, 300.0)}
        monkeypatch.setattr(rp, "fetch_account_snapshot", lambda *a, **k: _FakeSnapshot(positions=held))
        wl = tmp_path / "wl.txt"
        wl.write_text("TSLA\n# a comment\nNVDA\n", encoding="utf-8")
        monkeypatch.setattr(settings, "SYNC_WATCHLIST_FILES", str(wl))

        got = ps.resolve_universe("all")
        expected = sorted(
            {"NVDA", "TSLA"} | {t.upper() for t in settings.DEFAULT_TICKERS}
        )
        assert got == expected

    def test_all_passes_probe_market_false(self, monkeypatch):
        """resolve_universe must never trigger a per-symbol market probe (pure
        waste in a universe-resolve). Spy on build_sync_report's kwargs."""
        import data.robinhood_portfolio as rp
        from data import portfolio_sync as ps

        monkeypatch.setattr(rp, "fetch_account_snapshot", lambda *a, **k: None)
        seen = {}

        def _spy(snapshot, **kwargs):
            seen.update(kwargs)

            @dataclass
            class _R:
                symbols: dict[str, Any]

            return _R(symbols={})

        monkeypatch.setattr(ps, "build_sync_report", _spy)
        ps.resolve_universe("all")
        assert seen.get("probe_market") is False

    def test_all_never_calls_interactive_login(self, monkeypatch):
        """The 'all' path must use the non-interactive TOTP snapshot, never the
        stdin-prompting RobinhoodClient.login() (which would hang a cron job)."""
        import data.robinhood_portfolio as rp
        from data import portfolio_sync as ps
        from data import robinhood_client as rc

        def _no_login(self, *a, **k):  # pragma: no cover - must never run
            raise AssertionError("resolve_universe must not call interactive login")

        monkeypatch.setattr(rc.RobinhoodClient, "login", _no_login)
        monkeypatch.setattr(
            rp, "fetch_account_snapshot",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no creds")),
        )
        # Read via the `settings` singleton, not os.environ (fixed 2026-08).
        monkeypatch.setattr(rc.settings, "SYNC_WATCHLIST_FILES", None)
        # Completes (returns DEFAULT_TICKERS) without ever hitting login().
        assert ps.resolve_universe("all")


# ---------------------------------------------------------------------------
# resolve_universe — symbol-rating auto-drop (settings.SYMBOL_RATING_AUTO_DROP_ENABLED)
# ---------------------------------------------------------------------------


class _FakeRatingStore:
    """Minimal stand-in for rating.symbol_rating_store.SymbolRatingStore --
    only the surface resolve_universe() actually calls."""

    last_kwargs: dict[str, Any] | None = None

    def __init__(self, *, excluded=None, boom=False, readonly=False):
        self._excluded = excluded or set()
        self._boom = boom
        self.readonly = readonly

    def get_excluded_symbols(self, *, threshold_cycles, known_symbols=None):
        type(self).last_kwargs = {
            "threshold_cycles": threshold_cycles,
            "known_symbols": set(known_symbols) if known_symbols is not None else None,
        }
        if self._boom:
            raise RuntimeError("DB unavailable")
        return set(self._excluded)


class TestResolveUniverseSymbolRatingAutoDrop:
    def test_flag_off_never_touches_rating_store(self, monkeypatch):
        """Default (SYMBOL_RATING_AUTO_DROP_ENABLED=False) must be byte-identical
        to pre-change behavior -- the rating store is never even imported/called."""
        import data.robinhood_portfolio as rp
        from data import portfolio_sync as ps
        from settings import settings

        held = {"NVDA": _FakePosition("NVDA", 3, 100.0, 300.0, 300.0)}
        monkeypatch.setattr(rp, "fetch_account_snapshot", lambda *a, **k: _FakeSnapshot(positions=held))
        monkeypatch.setattr(settings, "SYNC_WATCHLIST_FILES", None)
        monkeypatch.setattr(settings, "SYMBOL_RATING_AUTO_DROP_ENABLED", False)

        def _boom(*a, **k):  # pragma: no cover - must never run
            raise AssertionError("rating store must not be constructed when the flag is off")

        import rating.symbol_rating_store as rating_store_mod
        monkeypatch.setattr(rating_store_mod, "SymbolRatingStore", _boom)

        got = ps.resolve_universe("all")
        expected = sorted({"NVDA"} | {t.upper() for t in settings.DEFAULT_TICKERS})
        assert got == expected

    def test_auto_drop_excludes_non_held_symbol(self, monkeypatch):
        import data.robinhood_portfolio as rp
        import rating.symbol_rating_store as rating_store_mod
        from data import portfolio_sync as ps
        from settings import settings

        held = {"NVDA": _FakePosition("NVDA", 3, 100.0, 300.0, 300.0)}
        monkeypatch.setattr(rp, "fetch_account_snapshot", lambda *a, **k: _FakeSnapshot(positions=held))
        monkeypatch.setattr(settings, "SYNC_WATCHLIST_FILES", None)
        monkeypatch.setattr(settings, "SYMBOL_RATING_AUTO_DROP_ENABLED", True)
        monkeypatch.setattr(settings, "SYMBOL_RATING_DROP_THRESHOLD_CYCLES", 5)
        monkeypatch.setattr(settings, "DEFAULT_TICKERS", ["NVDA", "BADSTOCK"])

        monkeypatch.setattr(
            rating_store_mod, "SymbolRatingStore",
            lambda **kw: _FakeRatingStore(excluded={"BADSTOCK"}, **kw),
        )

        got = ps.resolve_universe("all")
        assert "BADSTOCK" not in got
        assert "NVDA" in got

    def test_auto_drop_never_excludes_a_held_symbol(self, monkeypatch):
        """Non-negotiable safety invariant: even if the store (incorrectly, or
        due to a stale most-recent row) reports a held symbol as excluded,
        resolve_universe's own defensive subtraction must keep it."""
        import data.robinhood_portfolio as rp
        import rating.symbol_rating_store as rating_store_mod
        from data import portfolio_sync as ps
        from settings import settings

        held = {"NVDA": _FakePosition("NVDA", 3, 100.0, 300.0, 300.0)}
        monkeypatch.setattr(rp, "fetch_account_snapshot", lambda *a, **k: _FakeSnapshot(positions=held))
        monkeypatch.setattr(settings, "SYNC_WATCHLIST_FILES", None)
        monkeypatch.setattr(settings, "SYMBOL_RATING_AUTO_DROP_ENABLED", True)
        monkeypatch.setattr(settings, "SYMBOL_RATING_DROP_THRESHOLD_CYCLES", 5)
        monkeypatch.setattr(settings, "DEFAULT_TICKERS", [])

        # Store (incorrectly) reports the held symbol as excluded.
        monkeypatch.setattr(
            rating_store_mod, "SymbolRatingStore",
            lambda **kw: _FakeRatingStore(excluded={"NVDA"}, **kw),
        )

        got = ps.resolve_universe("all")
        assert "NVDA" in got

    def test_auto_drop_store_failure_fails_open(self, monkeypatch):
        """A DB hiccup in get_excluded_symbols must leave the universe
        completely unaffected (CONSTRAINT #6) -- never shrink it."""
        import data.robinhood_portfolio as rp
        import rating.symbol_rating_store as rating_store_mod
        from data import portfolio_sync as ps
        from settings import settings

        held = {"NVDA": _FakePosition("NVDA", 3, 100.0, 300.0, 300.0)}
        monkeypatch.setattr(rp, "fetch_account_snapshot", lambda *a, **k: _FakeSnapshot(positions=held))
        monkeypatch.setattr(settings, "SYNC_WATCHLIST_FILES", None)
        monkeypatch.setattr(settings, "SYMBOL_RATING_AUTO_DROP_ENABLED", True)
        monkeypatch.setattr(settings, "SYMBOL_RATING_DROP_THRESHOLD_CYCLES", 5)
        monkeypatch.setattr(settings, "DEFAULT_TICKERS", ["NVDA", "BADSTOCK"])

        monkeypatch.setattr(
            rating_store_mod, "SymbolRatingStore",
            lambda **kw: _FakeRatingStore(boom=True, **kw),
        )

        got = ps.resolve_universe("all")
        expected = sorted({"NVDA", "BADSTOCK"})
        assert got == expected


# ---------------------------------------------------------------------------
# compute_tracked_universe() / load_env_watchlist() -- the shared primitives
# behind the daemon universe-divergence fix (see
# docs/known_issues/daemon_universe_watchlist_divergence.md). main.py's
# _build_universe()/_load_watchlist() and pipeline/production_steps.py's
# AsyncDataFetchStep both delegate to these now instead of drifting.
# ---------------------------------------------------------------------------


class TestComputeTrackedUniverse:
    def test_union_of_held_watchlist_discovered(self, monkeypatch):
        from data import portfolio_sync as ps
        from settings import settings

        monkeypatch.setattr(settings, "SYMBOL_RATING_AUTO_DROP_ENABLED", False)
        got = ps.compute_tracked_universe(
            held=["aapl"], watchlist=["msft"], discovered=["nvda"], default_tickers=["ibm"],
        )
        assert got == ["AAPL", "MSFT", "NVDA"]

    def test_default_tickers_used_only_when_union_empty(self, monkeypatch):
        from data import portfolio_sync as ps
        from settings import settings

        monkeypatch.setattr(settings, "SYMBOL_RATING_AUTO_DROP_ENABLED", False)
        assert ps.compute_tracked_universe(default_tickers=["SPY"]) == ["SPY"]
        # Any non-empty input at all suppresses the fallback.
        got = ps.compute_tracked_universe(watchlist=["QQQ"], default_tickers=["SPY"])
        assert got == ["QQQ"]

    def test_everything_empty_returns_empty_list(self, monkeypatch):
        from data import portfolio_sync as ps
        from settings import settings

        monkeypatch.setattr(settings, "SYMBOL_RATING_AUTO_DROP_ENABLED", False)
        assert ps.compute_tracked_universe() == []

    def test_rating_exclusion_never_drops_held(self, monkeypatch):
        import rating.symbol_rating_store as rating_store_mod
        from data import portfolio_sync as ps
        from settings import settings

        monkeypatch.setattr(settings, "SYMBOL_RATING_AUTO_DROP_ENABLED", True)
        monkeypatch.setattr(settings, "SYMBOL_RATING_DROP_THRESHOLD_CYCLES", 5)
        monkeypatch.setattr(
            rating_store_mod, "SymbolRatingStore",
            lambda **kw: _FakeRatingStore(excluded={"NVDA", "MSFT"}, **kw),
        )

        got = ps.compute_tracked_universe(held=["NVDA"], watchlist=["MSFT"], discovered=["TSLA"])
        # NVDA is held -> never dropped even though the rating store excludes
        # it; MSFT is watchlist-only and correctly dropped; TSLA survives.
        assert got == ["NVDA", "TSLA"]

    def test_rating_exclusion_lookup_failure_fails_open(self, monkeypatch):
        import rating.symbol_rating_store as rating_store_mod
        from data import portfolio_sync as ps
        from settings import settings

        monkeypatch.setattr(settings, "SYMBOL_RATING_AUTO_DROP_ENABLED", True)
        monkeypatch.setattr(settings, "SYMBOL_RATING_DROP_THRESHOLD_CYCLES", 5)
        monkeypatch.setattr(
            rating_store_mod, "SymbolRatingStore",
            lambda **kw: _FakeRatingStore(boom=True, **kw),
        )

        got = ps.compute_tracked_universe(watchlist=["NVDA", "MSFT"])
        assert got == ["MSFT", "NVDA"]

    def test_apply_rating_exclusion_false_skips_the_store_entirely(self, monkeypatch):
        import rating.symbol_rating_store as rating_store_mod
        from data import portfolio_sync as ps
        from settings import settings

        monkeypatch.setattr(settings, "SYMBOL_RATING_AUTO_DROP_ENABLED", True)

        def _boom(**kw):
            raise AssertionError("SymbolRatingStore must not be constructed when apply_rating_exclusion=False")

        monkeypatch.setattr(rating_store_mod, "SymbolRatingStore", _boom)
        got = ps.compute_tracked_universe(watchlist=["NVDA"], apply_rating_exclusion=False)
        assert got == ["NVDA"]


class TestLoadEnvWatchlist:
    def test_env_var_only(self, monkeypatch, tmp_path):
        from data import portfolio_sync as ps

        monkeypatch.setenv("WATCHLIST", "aapl, msft")
        got = ps.load_env_watchlist(str(tmp_path / "does_not_exist.txt"))
        assert got == ["AAPL", "MSFT"]

    def test_file_only(self, monkeypatch, tmp_path):
        from data import portfolio_sync as ps

        monkeypatch.delenv("WATCHLIST", raising=False)
        wl = tmp_path / "watchlist.txt"
        wl.write_text("nvda\n# a comment\ntsla\n\n")
        got = ps.load_env_watchlist(str(wl))
        assert got == ["NVDA", "TSLA"]

    def test_env_and_file_merge_deduped(self, monkeypatch, tmp_path):
        from data import portfolio_sync as ps

        monkeypatch.setenv("WATCHLIST", "AAPL")
        wl = tmp_path / "watchlist.txt"
        wl.write_text("AAPL\nMSFT\n")
        got = ps.load_env_watchlist(str(wl))
        assert got == ["AAPL", "MSFT"]

    def test_neither_configured_returns_empty(self, monkeypatch, tmp_path):
        from data import portfolio_sync as ps

        monkeypatch.delenv("WATCHLIST", raising=False)
        got = ps.load_env_watchlist(str(tmp_path / "does_not_exist.txt"))
        assert got == []

    def test_env_var_inline_comment_artifact_is_rejected_not_fetched(self, monkeypatch, tmp_path, caplog):
        """Regression test for the 2026-08 pipeline hang: python-dotenv does not
        strip a trailing '# comment' from an unquoted, otherwise-blank value, so
        WATCHLIST=                    # Plain text comma-separated fallback ticker list
        sets the real env var to the literal comment text. That text must never
        be treated as a ticker and handed to a live bars fetch."""
        from data import portfolio_sync as ps

        monkeypatch.setenv(
            "WATCHLIST", "# Plain text comma-separated fallback ticker list"
        )
        with caplog.at_level("WARNING"):
            got = ps.load_env_watchlist(str(tmp_path / "does_not_exist.txt"))
        assert got == []
        assert "rejected" in caplog.text
        assert "PLAIN TEXT COMMA-SEPARATED FALLBACK TICKER LIST" in caplog.text

    def test_env_var_mixed_valid_and_garbage_keeps_only_valid(self, monkeypatch, tmp_path):
        from data import portfolio_sync as ps

        monkeypatch.setenv("WATCHLIST", "aapl, # a stray comment fragment, msft")
        got = ps.load_env_watchlist(str(tmp_path / "does_not_exist.txt"))
        assert got == ["AAPL", "MSFT"]

    def test_file_ticker_with_embedded_whitespace_is_rejected(self, monkeypatch, tmp_path):
        from data import portfolio_sync as ps

        monkeypatch.delenv("WATCHLIST", raising=False)
        wl = tmp_path / "watchlist.txt"
        # A line that survives the '#'-prefix comment filter (doesn't start
        # with '#') but is obviously not a real ticker.
        wl.write_text("nvda\nnot a real ticker\ntsla\n")
        got = ps.load_env_watchlist(str(wl))
        assert got == ["NVDA", "TSLA"]

    def test_implausibly_long_candidate_is_rejected(self, monkeypatch, tmp_path):
        from data import portfolio_sync as ps

        monkeypatch.setenv("WATCHLIST", "AAPL," + "X" * 40)
        got = ps.load_env_watchlist(str(tmp_path / "does_not_exist.txt"))
        assert got == ["AAPL"]

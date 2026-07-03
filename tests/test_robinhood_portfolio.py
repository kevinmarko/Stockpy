# =============================================================================
# TESTS: data/robinhood_portfolio.py
# File: tests/test_robinhood_portfolio.py
#
# All tests are fully offline — no Robinhood network calls are made.
# robin_stocks functions and _login() are monkeypatched.
#
# Coverage:
#   PortfolioPosition   — round-trip serialisation, frozen immutability
#   AccountSnapshot     — round-trip JSON, age_hours, is_stale, UTC tz-aware,
#                         no secrets in serialised payload
#   Cache I/O           — write-then-read, missing file → None, corrupt → None
#   fetch_account_snapshot
#                       — cache hit (no live call), cache miss (live + write),
#                         force=True bypasses fresh cache, live fail + stale
#                         cache → stale snapshot, live fail + no cache → raises
#   Dividend logic      — paid + reinvested counted; pending excluded;
#                         UUID correlation via instrument URL
#   Unrealized P/L math — computed correctly from holdings data
#   Per-symbol isolation— one bad position does not abort the rest
#   Safety audit        — module must not reference any order/execution fn
# =============================================================================

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Stub env vars BEFORE importing the module under test.
# (The module reads os.environ at function-call time, not import time, but
# the import still triggers top-level module code so we guard here too.)
# ---------------------------------------------------------------------------
os.environ.setdefault("RH_USERNAME", "test@example.com")
os.environ.setdefault("RH_PASSWORD", "testpassword123")
os.environ.setdefault("RH_MFA_SECRET", "JBSWY3DPEHPK3PXP")  # RFC 6238 test vector

from data.robinhood_portfolio import (  # noqa: E402
    AccountSnapshot,
    PortfolioPosition,
    _fetch_live_snapshot,
    _read_cache,
    _write_cache,
    fetch_account_snapshot,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_position(
    symbol: str = "AAPL",
    quantity: float = 10.0,
    average_cost: float = 150.0,
    current_price: float = 180.0,
    dividends_received: float = 5.0,
) -> PortfolioPosition:
    market_value = quantity * current_price
    cost_basis = quantity * average_cost
    unrealized_pl = market_value - cost_basis
    unrealized_pl_pct = unrealized_pl / cost_basis * 100.0
    return PortfolioPosition(
        symbol=symbol,
        quantity=quantity,
        average_cost=average_cost,
        current_price=current_price,
        market_value=market_value,
        unrealized_pl=unrealized_pl,
        unrealized_pl_pct=unrealized_pl_pct,
        dividends_received=dividends_received,
        name=f"{symbol} Inc.",
    )


def _make_snapshot(age_hours: float = 0.0) -> AccountSnapshot:
    """Return a synthetic AccountSnapshot with the given age."""
    fetched = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    pos = _make_position()
    return AccountSnapshot(
        positions={"AAPL": pos},
        buying_power=500.0,
        total_equity=2300.0,
        total_dividends=5.0,
        fetched_at=fetched,
    )


# ---------------------------------------------------------------------------
# PortfolioPosition
# ---------------------------------------------------------------------------

class TestPortfolioPosition:
    def test_round_trip_serialisation(self) -> None:
        pos = _make_position(symbol="MSFT", quantity=5.0, average_cost=300.0,
                             current_price=420.0, dividends_received=12.50)
        restored = PortfolioPosition.from_dict(pos.to_dict())
        assert restored == pos

    def test_to_dict_all_fields_present(self) -> None:
        pos = _make_position()
        d = pos.to_dict()
        for field in (
            "symbol", "quantity", "average_cost", "current_price",
            "market_value", "unrealized_pl", "unrealized_pl_pct",
            "dividends_received", "name",
        ):
            assert field in d, f"Missing field '{field}' in to_dict() output"

    def test_frozen_raises_on_mutation(self) -> None:
        pos = _make_position()
        with pytest.raises((AttributeError, TypeError)):
            pos.quantity = 99.0  # type: ignore[misc]

    def test_from_dict_coerces_strings_to_float(self) -> None:
        d = {
            "symbol": "JNJ",
            "quantity": "3",
            "average_cost": "120.5",
            "current_price": "155.0",
            "market_value": "465.0",
            "unrealized_pl": "103.5",
            "unrealized_pl_pct": "28.71",
            "dividends_received": "4.25",
            "name": "Johnson & Johnson",
        }
        pos = PortfolioPosition.from_dict(d)
        assert pos.quantity == pytest.approx(3.0)
        assert pos.average_cost == pytest.approx(120.5)


# ---------------------------------------------------------------------------
# AccountSnapshot
# ---------------------------------------------------------------------------

class TestAccountSnapshot:
    def test_round_trip_json(self) -> None:
        snap = _make_snapshot(age_hours=1.0)
        blob = json.dumps(snap.to_dict())
        restored = AccountSnapshot.from_dict(json.loads(blob))
        assert restored.buying_power == pytest.approx(snap.buying_power)
        assert restored.total_equity == pytest.approx(snap.total_equity)
        assert "AAPL" in restored.positions
        assert restored.positions["AAPL"].symbol == "AAPL"

    def test_fetched_at_is_utc(self) -> None:
        snap = _make_snapshot()
        assert snap.fetched_at.tzinfo is not None
        assert snap.fetched_at.tzinfo == timezone.utc

    def test_age_hours_fresh(self) -> None:
        snap = _make_snapshot(age_hours=0.0)
        assert snap.age_hours() < 0.1  # within 6 seconds of "now"

    def test_age_hours_five_hours(self) -> None:
        snap = _make_snapshot(age_hours=5.0)
        assert 4.9 < snap.age_hours() < 5.1

    def test_is_stale_returns_false_for_fresh(self) -> None:
        snap = _make_snapshot(age_hours=1.0)
        assert not snap.is_stale(max_age_hours=20.0)

    def test_is_stale_returns_true_for_old(self) -> None:
        snap = _make_snapshot(age_hours=21.0)
        assert snap.is_stale(max_age_hours=20.0)

    def test_no_secrets_in_serialised_dict(self) -> None:
        snap = _make_snapshot()
        blob = json.dumps(snap.to_dict()).lower()
        for forbidden in ("password", "mfa_secret", "access_token", "rh_password"):
            assert forbidden not in blob, (
                f"Secret key '{forbidden}' found in serialised snapshot"
            )

    def test_fetched_at_preserved_across_json_round_trip(self) -> None:
        snap = _make_snapshot(age_hours=3.5)
        restored = AccountSnapshot.from_dict(json.loads(json.dumps(snap.to_dict())))
        # timezone-aware datetime must survive the round-trip
        diff = abs((restored.fetched_at - snap.fetched_at).total_seconds())
        assert diff < 1.0, "fetched_at changed by more than 1s across JSON round-trip"


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------

class TestCache:
    def test_write_then_read_roundtrip(self, tmp_path, monkeypatch) -> None:
        cache_file = tmp_path / "account_snapshot.json"
        monkeypatch.setattr("data.robinhood_portfolio._CACHE_PATH", cache_file)
        snap = _make_snapshot()
        _write_cache(snap)
        assert cache_file.exists()
        loaded = _read_cache()
        assert loaded is not None
        assert loaded.buying_power == pytest.approx(snap.buying_power)
        assert loaded.total_equity == pytest.approx(snap.total_equity)

    def test_read_missing_returns_none(self, tmp_path, monkeypatch) -> None:
        cache_file = tmp_path / "nonexistent_snapshot.json"
        monkeypatch.setattr("data.robinhood_portfolio._CACHE_PATH", cache_file)
        assert _read_cache() is None

    def test_read_corrupt_json_returns_none(self, tmp_path, monkeypatch) -> None:
        cache_file = tmp_path / "corrupt.json"
        cache_file.write_text("this is not valid json {{{{")
        monkeypatch.setattr("data.robinhood_portfolio._CACHE_PATH", cache_file)
        assert _read_cache() is None

    def test_cache_dir_created_when_absent(self, tmp_path, monkeypatch) -> None:
        cache_file = tmp_path / "new_subdir" / "account_snapshot.json"
        monkeypatch.setattr("data.robinhood_portfolio._CACHE_PATH", cache_file)
        _write_cache(_make_snapshot())
        assert cache_file.exists()

    def test_write_is_atomic_tmp_removed_on_success(self, tmp_path, monkeypatch) -> None:
        cache_file = tmp_path / "account_snapshot.json"
        monkeypatch.setattr("data.robinhood_portfolio._CACHE_PATH", cache_file)
        _write_cache(_make_snapshot())
        tmp_file = cache_file.with_suffix(".tmp")
        assert not tmp_file.exists(), ".tmp file left behind after successful write"


# ---------------------------------------------------------------------------
# fetch_account_snapshot — caching behaviour
# ---------------------------------------------------------------------------

class TestFetchAccountSnapshot:
    """All network I/O is suppressed via monkeypatching."""

    @pytest.fixture(autouse=True)
    def _no_db(self, monkeypatch):
        """Patch out the DB tier so existing tests only see JSON cache + live path."""
        from unittest.mock import MagicMock
        mock_store = MagicMock()
        mock_store.latest_account_snapshot.return_value = None
        mock_store.save_account_snapshot.return_value = 1
        monkeypatch.setattr("data.historical_store.HistoricalStore", lambda **kw: mock_store)

    def _setup(self, monkeypatch, tmp_path, live_snap=None):
        """Redirect cache path and wire in a fake live-fetch function."""
        cache_file = tmp_path / "account_snapshot.json"
        monkeypatch.setattr("data.robinhood_portfolio._CACHE_PATH", cache_file)
        live_called = []

        def fake_live():
            live_called.append(True)
            return live_snap or _make_snapshot(age_hours=0.0)

        monkeypatch.setattr("data.robinhood_portfolio._fetch_live_snapshot", fake_live)
        return cache_file, live_called

    def test_fresh_cache_hit_no_live_call(self, tmp_path, monkeypatch) -> None:
        """A non-stale cache must be returned without any live network call."""
        cache_file, live_called = self._setup(monkeypatch, tmp_path)

        # Pre-seed the cache with a 1-hour-old snapshot (well within the 20 h window)
        fresh_cached = _make_snapshot(age_hours=1.0)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(fresh_cached.to_dict()))

        result = fetch_account_snapshot(max_age_hours=20.0, force=False)

        assert not live_called, "Live fetch was called despite fresh cache"
        assert result.buying_power == pytest.approx(fresh_cached.buying_power)

    def test_missing_cache_triggers_live_fetch(self, tmp_path, monkeypatch) -> None:
        fresh = _make_snapshot(age_hours=0.0)
        cache_file, live_called = self._setup(monkeypatch, tmp_path, live_snap=fresh)

        result = fetch_account_snapshot(max_age_hours=20.0, force=False)

        assert live_called, "Live fetch was NOT called despite missing cache"
        assert result.total_equity == pytest.approx(fresh.total_equity)
        assert cache_file.exists(), "Cache file was not written after live fetch"

    def test_stale_cache_triggers_live_fetch(self, tmp_path, monkeypatch) -> None:
        """Cache older than max_age_hours must trigger a live refresh."""
        live_fresh = _make_snapshot(age_hours=0.0)
        # Give the fresh snapshot a marker value
        live_fresh_marked = AccountSnapshot(
            positions=live_fresh.positions,
            buying_power=77777.0,
            total_equity=live_fresh.total_equity,
            total_dividends=live_fresh.total_dividends,
            fetched_at=live_fresh.fetched_at,
        )
        cache_file, live_called = self._setup(monkeypatch, tmp_path, live_snap=live_fresh_marked)

        # Pre-seed a stale (25 h old) cache
        stale = _make_snapshot(age_hours=25.0)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(stale.to_dict()))

        result = fetch_account_snapshot(max_age_hours=20.0, force=False)

        assert live_called, "Live fetch was NOT called despite stale cache"
        assert result.buying_power == pytest.approx(77777.0)

    def test_force_bypasses_fresh_cache(self, tmp_path, monkeypatch) -> None:
        live_fresh_marked = AccountSnapshot(
            positions={},
            buying_power=88888.0,
            total_equity=0.0,
            total_dividends=0.0,
            fetched_at=datetime.now(timezone.utc),
        )
        cache_file, live_called = self._setup(monkeypatch, tmp_path, live_snap=live_fresh_marked)

        # Pre-seed a fresh cache (would NOT be refreshed without force=True)
        fresh_cached = _make_snapshot(age_hours=1.0)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(fresh_cached.to_dict()))

        result = fetch_account_snapshot(max_age_hours=20.0, force=True)

        assert live_called, "Live fetch was NOT called despite force=True"
        assert result.buying_power == pytest.approx(88888.0)

    def test_live_fail_returns_stale_cache(self, tmp_path, monkeypatch) -> None:
        """Live-fetch failure with an existing cache must return the stale cache."""
        cache_file = tmp_path / "account_snapshot.json"
        monkeypatch.setattr("data.robinhood_portfolio._CACHE_PATH", cache_file)

        old_snap = _make_snapshot(age_hours=25.0)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(old_snap.to_dict()))

        def fail_live():
            raise ConnectionError("Robinhood unreachable")

        monkeypatch.setattr("data.robinhood_portfolio._fetch_live_snapshot", fail_live)

        result = fetch_account_snapshot(max_age_hours=20.0)

        assert result.is_stale(max_age_hours=20.0), "Returned snapshot should be stale"
        assert result.buying_power == pytest.approx(old_snap.buying_power)

    def test_live_fail_no_cache_raises(self, tmp_path, monkeypatch) -> None:
        """Live-fetch failure with no cache at all must re-raise the exception."""
        cache_file = tmp_path / "account_snapshot.json"
        monkeypatch.setattr("data.robinhood_portfolio._CACHE_PATH", cache_file)

        def fail_live():
            raise ConnectionError("Robinhood unreachable")

        monkeypatch.setattr("data.robinhood_portfolio._fetch_live_snapshot", fail_live)

        with pytest.raises(ConnectionError):
            fetch_account_snapshot(max_age_hours=20.0)


# ---------------------------------------------------------------------------
# Dividend correlation logic
# ---------------------------------------------------------------------------

# Shared synthetic Robinhood API responses used by dividend + P/L tests
_MOCK_HOLDINGS = {
    "AAPL": {
        "quantity": "10.0",
        "average_buy_price": "150.00",
        "equity": "1800.00",
        "price": "180.00",
        "name": "Apple Inc.",
        "id": "aapl-instrument-uuid",
    },
    "MSFT": {
        "quantity": "5.0",
        "average_buy_price": "300.00",
        "equity": "2100.00",
        "price": "420.00",
        "name": "Microsoft Corporation",
        "id": "msft-instrument-uuid",
    },
}

_MOCK_DIVIDENDS = [
    {
        "state": "paid",
        "amount": "3.00",
        "instrument": "https://api.robinhood.com/instruments/aapl-instrument-uuid/",
    },
    {
        "state": "reinvested",
        "amount": "2.00",
        "instrument": "https://api.robinhood.com/instruments/aapl-instrument-uuid/",
    },
    {
        "state": "pending",    # must NOT be counted
        "amount": "99.00",
        "instrument": "https://api.robinhood.com/instruments/aapl-instrument-uuid/",
    },
    {
        "state": "paid",
        "amount": "4.50",
        "instrument": "https://api.robinhood.com/instruments/msft-instrument-uuid/",
    },
    {
        "state": "scheduled",  # must NOT be counted
        "amount": "10.00",
        "instrument": "https://api.robinhood.com/instruments/msft-instrument-uuid/",
    },
]


def _patch_robinhood(monkeypatch, holdings=None, dividends=None,
                     portfolio=None, account=None):
    """Apply monkeypatches for all robin_stocks calls used by _fetch_live_snapshot."""
    monkeypatch.setattr("data.robinhood_portfolio._login", lambda: None)
    monkeypatch.setattr(
        "data.robinhood_portfolio.r.build_holdings",
        lambda: holdings if holdings is not None else _MOCK_HOLDINGS,
    )
    monkeypatch.setattr(
        "data.robinhood_portfolio.r.get_dividends",
        lambda: dividends if dividends is not None else _MOCK_DIVIDENDS,
    )
    monkeypatch.setattr(
        "data.robinhood_portfolio.r.load_portfolio_profile",
        lambda: portfolio if portfolio is not None else {"equity": "3900.00"},
    )
    monkeypatch.setattr(
        "data.robinhood_portfolio.r.load_account_profile",
        lambda: account if account is not None else {"buying_power": "500.00"},
    )


class TestDividendCorrelation:
    def test_paid_and_reinvested_counted(self, monkeypatch) -> None:
        _patch_robinhood(monkeypatch)
        snap = _fetch_live_snapshot()
        # AAPL: $3 paid + $2 reinvested = $5; $99 pending excluded
        assert snap.positions["AAPL"].dividends_received == pytest.approx(5.0)

    def test_pending_excluded(self, monkeypatch) -> None:
        _patch_robinhood(monkeypatch)
        snap = _fetch_live_snapshot()
        # The $99 "pending" AAPL dividend must not appear
        assert snap.positions["AAPL"].dividends_received == pytest.approx(5.0)

    def test_scheduled_excluded(self, monkeypatch) -> None:
        _patch_robinhood(monkeypatch)
        snap = _fetch_live_snapshot()
        # The $10 "scheduled" MSFT dividend must not appear
        assert snap.positions["MSFT"].dividends_received == pytest.approx(4.5)

    def test_total_dividends_sum(self, monkeypatch) -> None:
        _patch_robinhood(monkeypatch)
        snap = _fetch_live_snapshot()
        # $3 + $2 (AAPL) + $4.50 (MSFT) = $9.50; pending/scheduled excluded
        assert snap.total_dividends == pytest.approx(9.5)

    def test_uuid_extracted_from_instrument_url(self, monkeypatch) -> None:
        """Dividend correlation depends on stripping the trailing slash and
        taking the last URL segment as the instrument UUID."""
        _patch_robinhood(monkeypatch)
        snap = _fetch_live_snapshot()
        # If UUID extraction is wrong, dividends would be 0 for both symbols
        assert snap.positions["AAPL"].dividends_received > 0
        assert snap.positions["MSFT"].dividends_received > 0

    def test_unknown_instrument_skipped_gracefully(self, monkeypatch) -> None:
        """A dividend with an unrecognised instrument UUID is counted in
        total_dividends but not assigned to any symbol."""
        divs = [
            {
                "state": "paid",
                "amount": "1.00",
                "instrument": "https://api.robinhood.com/instruments/unknown-uuid/",
            }
        ]
        _patch_robinhood(monkeypatch, dividends=divs)
        snap = _fetch_live_snapshot()
        assert snap.total_dividends == pytest.approx(1.0)
        for pos in snap.positions.values():
            assert pos.dividends_received == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Unrealized P/L math
# ---------------------------------------------------------------------------

class TestUnrealizedPL:
    def test_pl_and_pct_correct(self, monkeypatch) -> None:
        holdings = {
            "TSLA": {
                "quantity": "2.0",
                "average_buy_price": "200.00",
                "equity": "500.00",   # Robinhood-provided market value
                "price": "250.00",
                "name": "Tesla Inc.",
                "id": "tsla-uuid",
            }
        }
        _patch_robinhood(monkeypatch, holdings=holdings, dividends=[],
                         portfolio={"equity": "500.00"},
                         account={"buying_power": "100.00"})
        snap = _fetch_live_snapshot()
        pos = snap.positions["TSLA"]
        # market_value = equity = 500
        # cost_basis   = 2 * 200 = 400
        # unrealized_pl = 500 - 400 = 100
        # unrealized_pl_pct = 100 / 400 * 100 = 25 %
        assert pos.market_value == pytest.approx(500.0)
        assert pos.unrealized_pl == pytest.approx(100.0)
        assert pos.unrealized_pl_pct == pytest.approx(25.0)

    def test_pl_negative_when_underwater(self, monkeypatch) -> None:
        holdings = {
            "XOM": {
                "quantity": "5.0",
                "average_buy_price": "120.00",
                "equity": "500.00",   # 5 * 100
                "price": "100.00",
                "name": "ExxonMobil",
                "id": "xom-uuid",
            }
        }
        _patch_robinhood(monkeypatch, holdings=holdings, dividends=[],
                         portfolio={"equity": "500.00"},
                         account={"buying_power": "0.00"})
        snap = _fetch_live_snapshot()
        pos = snap.positions["XOM"]
        assert pos.unrealized_pl == pytest.approx(-100.0)
        assert pos.unrealized_pl_pct == pytest.approx(-100.0 / 600.0 * 100.0)

    def test_equity_field_fallback_to_qty_times_price(self, monkeypatch) -> None:
        """When the 'equity' field is missing, fall back to quantity * price."""
        holdings = {
            "NFLX": {
                "quantity": "3.0",
                "average_buy_price": "400.00",
                "equity": None,        # missing — must fall back
                "price": "450.00",
                "name": "Netflix",
                "id": "nflx-uuid",
            }
        }
        _patch_robinhood(monkeypatch, holdings=holdings, dividends=[],
                         portfolio={"equity": "1350.00"},
                         account={"buying_power": "50.00"})
        snap = _fetch_live_snapshot()
        pos = snap.positions["NFLX"]
        assert pos.market_value == pytest.approx(3.0 * 450.0)


# ---------------------------------------------------------------------------
# Per-symbol exception isolation
# ---------------------------------------------------------------------------

class TestPositionIsolation:
    def test_bad_position_skipped_good_position_retained(self, monkeypatch) -> None:
        holdings = {
            "GOOD": {
                "quantity": "1.0",
                "average_buy_price": "100.00",
                "equity": "110.00",
                "price": "110.00",
                "name": "Good Corp",
                "id": "good-uuid",
            },
            "BAD": {
                "quantity": "NOT_A_NUMBER",   # will cause ValueError
                "average_buy_price": "50.00",
                "equity": None,
                "price": None,
                "name": "Bad Corp",
                "id": "bad-uuid",
            },
        }
        _patch_robinhood(monkeypatch, holdings=holdings, dividends=[],
                         portfolio={"equity": "110.00"},
                         account={"buying_power": "200.00"})
        snap = _fetch_live_snapshot()
        assert "GOOD" in snap.positions, "Valid position was dropped"
        assert "BAD" not in snap.positions, "Invalid position was not dropped"

    def test_all_none_fields_produce_zero_position(self, monkeypatch) -> None:
        """All-None holdings fields produce a zero-filled (not skipped) position.

        The module guards against None via ``data.get(...) or 0.0`` before
        calling float(), so None fields saturate to 0.0 instead of raising.
        This is intentional: a zero-quantity position is a valid edge-case
        (fractional share rounding, cash-position placeholder) and is surfaced
        to the caller rather than silently dropped.
        """
        holdings = {
            "JUNK": {
                "quantity": None,
                "average_buy_price": None,
                "equity": None,
                "price": None,
                "name": None,
                "id": "junk-uuid",
            },
        }
        _patch_robinhood(monkeypatch, holdings=holdings, dividends=[],
                         portfolio={"equity": "0.00"},
                         account={"buying_power": "0.00"})
        snap = _fetch_live_snapshot()
        # All-None fields → zero-filled position is built, not skipped.
        # The module uses ``data.get(...) or 0.0`` guards before float().
        assert "JUNK" in snap.positions
        pos = snap.positions["JUNK"]
        assert pos.quantity == 0.0
        assert pos.market_value == 0.0
        assert pos.name == "JUNK"  # falls back to symbol when name is None


# ---------------------------------------------------------------------------
# Account-level equity and buying power
# ---------------------------------------------------------------------------

class TestAccountLevelFields:
    def test_equity_and_buying_power_populated(self, monkeypatch) -> None:
        _patch_robinhood(monkeypatch,
                         portfolio={"equity": "12345.67"},
                         account={"buying_power": "999.99"})
        snap = _fetch_live_snapshot()
        assert snap.total_equity == pytest.approx(12345.67)
        assert snap.buying_power == pytest.approx(999.99)

    def test_extended_hours_equity_fallback(self, monkeypatch) -> None:
        """When 'equity' is absent, fall back to 'extended_hours_equity'."""
        _patch_robinhood(monkeypatch,
                         portfolio={"extended_hours_equity": "9876.54"},
                         account={"buying_power": "0.00"})
        snap = _fetch_live_snapshot()
        assert snap.total_equity == pytest.approx(9876.54)

    def test_buying_power_cash_fallback(self, monkeypatch) -> None:
        """When 'buying_power' is absent, fall back to 'cash'."""
        _patch_robinhood(monkeypatch,
                         portfolio={"equity": "1000.00"},
                         account={"cash": "250.00"})
        snap = _fetch_live_snapshot()
        assert snap.buying_power == pytest.approx(250.0)


# ---------------------------------------------------------------------------
# Safety audit — no order/execution function references
# ---------------------------------------------------------------------------

def test_no_order_functions_in_module_source() -> None:
    """Confirm the module contains no order-submission or execution function names.

    This is a static safety check: any of these names in the module source
    would indicate an accidental introduction of execution capability.
    """
    import inspect
    import data.robinhood_portfolio as mod

    source = inspect.getsource(mod)
    forbidden_patterns = [
        "place_order",
        "submit_order",
        "cancel_order",
        "order_buy",
        "order_sell",
        "buy_stock_market",
        "sell_stock_market",
        "create_order",
        "modify_order",
        "order_option",
    ]
    for pattern in forbidden_patterns:
        assert pattern not in source, (
            f"Forbidden execution-function reference '{pattern}' found in "
            f"data/robinhood_portfolio.py"
        )


# ---------------------------------------------------------------------------
# Login Flow Tests
# ---------------------------------------------------------------------------

class TestLoginFlow:
    def test_login_with_mfa_secret(self, monkeypatch) -> None:
        """When RH_MFA_SECRET is set, login should use TOTP and by_sms=False."""
        login_calls = []

        def mock_login(username, password, store_session=True, mfa_code=None, by_sms=False):
            login_calls.append({
                "username": username,
                "password": password,
                "store_session": store_session,
                "mfa_code": mfa_code,
                "by_sms": by_sms
            })
            return {"access_token": "mock-totp-token"}

        monkeypatch.setattr("data.robinhood_portfolio.r.login", mock_login)
        monkeypatch.setenv("RH_USERNAME", "totp_user@example.com")
        monkeypatch.setenv("RH_PASSWORD", "totp_pass")
        monkeypatch.setenv("RH_MFA_SECRET", "JBSWY3DPEHPK3PXP")

        from data.robinhood_portfolio import _login
        _login()

        assert len(login_calls) == 1
        assert login_calls[0]["username"] == "totp_user@example.com"
        assert login_calls[0]["password"] == "totp_pass"
        assert login_calls[0]["mfa_code"] is not None
        assert login_calls[0]["by_sms"] is False

    def test_login_without_mfa_secret_sms_fallback(self, monkeypatch) -> None:
        """When RH_MFA_SECRET is empty/missing, login should fall back to SMS MFA (by_sms=True)."""
        login_calls = []

        def mock_login(username, password, store_session=True, mfa_code=None, by_sms=True):
            login_calls.append({
                "username": username,
                "password": password,
                "store_session": store_session,
                "mfa_code": mfa_code,
                "by_sms": by_sms
            })
            return {"access_token": "mock-sms-token"}

        monkeypatch.setattr("data.robinhood_portfolio.r.login", mock_login)
        monkeypatch.setenv("RH_USERNAME", "sms_user@example.com")
        monkeypatch.setenv("RH_PASSWORD", "sms_pass")
        monkeypatch.delenv("RH_MFA_SECRET", raising=False)

        from data.robinhood_portfolio import _login
        _login()

        assert len(login_calls) == 1
        assert login_calls[0]["username"] == "sms_user@example.com"
        assert login_calls[0]["password"] == "sms_pass"
        assert login_calls[0]["mfa_code"] is None
        assert login_calls[0]["by_sms"] is True

    def test_login_failures(self, monkeypatch) -> None:
        """If login fails (does not return dict with access_token), raise RuntimeError."""
        def mock_login(username, password, **kwargs):
            return None

        monkeypatch.setattr("data.robinhood_portfolio.r.login", mock_login)
        monkeypatch.setenv("RH_USERNAME", "user@example.com")
        monkeypatch.setenv("RH_PASSWORD", "pass")
        monkeypatch.setenv("RH_MFA_SECRET", "JBSWY3DPEHPK3PXP")

        from data.robinhood_portfolio import _login
        with pytest.raises(RuntimeError, match="Robinhood login failed"):
            _login()


# ---------------------------------------------------------------------------
# DB Integration Tests (Tier 2.3 Phase 2)
# ---------------------------------------------------------------------------

class TestDBIntegration:
    """Verify the three-tier read order: DB → JSON cache → live."""

    def _setup_no_live(self, monkeypatch, tmp_path):
        """Redirect _CACHE_PATH to a temp file and wire a sentinel live-fetch."""
        cache_file = tmp_path / "account_snapshot.json"
        monkeypatch.setattr("data.robinhood_portfolio._CACHE_PATH", cache_file)
        live_calls: list = []

        def _no_live():
            live_calls.append(True)
            raise RuntimeError("live fetch must not be called in this test")

        monkeypatch.setattr("data.robinhood_portfolio._fetch_live_snapshot", _no_live)
        return cache_file, live_calls

    def test_db_read_path_used_when_fresh(self, tmp_path, monkeypatch):
        """DB-cached fresh snapshot → no live fetch and no JSON read."""
        from data.historical_store import HistoricalStore

        # Redirect JSON cache to an empty path and wire live-sentinel
        cache_file, live_calls = self._setup_no_live(monkeypatch, tmp_path)

        # Build a fresh snapshot and persist it in a temp DB
        db_path = str(tmp_path / "test.db")
        store = HistoricalStore(db_path=db_path)
        fresh = _make_snapshot(age_hours=1.0)
        store.save_account_snapshot(fresh)

        # Patch HistoricalStore so it returns our pre-seeded store
        def _make_store():
            return HistoricalStore(db_path=db_path)

        monkeypatch.setattr("data.historical_store.HistoricalStore", _make_store)

        result = fetch_account_snapshot(max_age_hours=20.0, force=False)

        assert not live_calls, "Live fetch must NOT have been called"
        assert result.buying_power == pytest.approx(fresh.buying_power)
        assert result.total_equity == pytest.approx(fresh.total_equity)
        # JSON cache must not have been written (no live fetch happened)
        assert not cache_file.exists()

    def test_falls_through_to_json_on_db_error(self, tmp_path, monkeypatch):
        """DB error → falls through to JSON cache, never crashes."""
        cache_file = tmp_path / "account_snapshot.json"
        monkeypatch.setattr("data.robinhood_portfolio._CACHE_PATH", cache_file)

        # Seed the JSON cache with a fresh snapshot
        fresh = _make_snapshot(age_hours=1.0)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(fresh.to_dict()))

        # Wire live-fetch to a sentinel that fails if reached
        monkeypatch.setattr(
            "data.robinhood_portfolio._fetch_live_snapshot",
            lambda: (_ for _ in ()).throw(RuntimeError("must not reach live")),
        )

        # Make HistoricalStore raise on instantiation
        def _broken_store():
            raise RuntimeError("DB unavailable")

        monkeypatch.setattr("data.historical_store.HistoricalStore", _broken_store)

        result = fetch_account_snapshot(max_age_hours=20.0, force=False)

        assert result is not None, "Expected JSON fallback to succeed"
        assert result.buying_power == pytest.approx(fresh.buying_power)
        assert result.total_equity == pytest.approx(fresh.total_equity)

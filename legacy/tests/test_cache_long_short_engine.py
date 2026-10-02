"""tests/test_cache_long_short_engine.py

Real assertions against synthetic data -- not smoke tests. HistoricalStore/
get_provider/analyze_pair are mocked at the engine module's import site
(so a real DB/network call never happens); CacheLongShortStore is backed by
a real in-memory SQLite instance so wash-sale/TLH-flag persistence is
exercised against the actual ORM, not a MagicMock standing in for SQL
semantics.
"""
import datetime
import math

import pandas as pd
import pytest

from data.cache_long_short_store import CacheLongShortStore
from engine.cache_long_short_engine import CacheLongShortEngine


@pytest.fixture
def store(monkeypatch):
    """A real in-memory CacheLongShortStore, patched in as the engine's
    CacheLongShortStore() singleton for the duration of the test."""
    s = CacheLongShortStore(db_url="sqlite:///:memory:")
    monkeypatch.setattr(
        "engine.cache_long_short_engine.CacheLongShortStore", lambda *a, **k: s
    )
    return s


def _bars(prices):
    idx = pd.date_range(end=datetime.datetime.now(), periods=len(prices), freq="D")
    return pd.DataFrame({"Close": prices}, index=idx)


# ---------------------------------------------------------------------------
# calculate_beta
# ---------------------------------------------------------------------------


class TestCalculateBeta:
    def test_returns_last_rolling_value(self, monkeypatch):
        mock_store = type(
            "S", (), {"get_bars": lambda self, ticker, lookback_days=90: _bars([100.0] * 90)}
        )()
        monkeypatch.setattr(
            "engine.cache_long_short_engine.HistoricalStore", lambda: mock_store
        )
        monkeypatch.setattr(
            "engine.cache_long_short_engine.calculate_rolling_beta",
            lambda price_df, spy_df, window: pd.Series([1.1, 1.2, 1.3]),
        )
        beta = CacheLongShortEngine.calculate_beta("AAPL", window=60)
        assert beta == 1.3

    def test_insufficient_ticker_history_returns_none(self, monkeypatch):
        mock_store = type(
            "S", (), {"get_bars": lambda self, ticker, lookback_days=90: pd.DataFrame()}
        )()
        monkeypatch.setattr(
            "engine.cache_long_short_engine.HistoricalStore", lambda: mock_store
        )
        assert CacheLongShortEngine.calculate_beta("NEWCO", window=60) is None

    def test_non_finite_result_returns_none(self, monkeypatch):
        mock_store = type(
            "S", (), {"get_bars": lambda self, ticker, lookback_days=90: _bars([100.0] * 90)}
        )()
        monkeypatch.setattr(
            "engine.cache_long_short_engine.HistoricalStore", lambda: mock_store
        )
        monkeypatch.setattr(
            "engine.cache_long_short_engine.calculate_rolling_beta",
            lambda price_df, spy_df, window: pd.Series([float("inf")]),
        )
        assert CacheLongShortEngine.calculate_beta("AAPL", window=60) is None


# ---------------------------------------------------------------------------
# find_correlated_proxy
# ---------------------------------------------------------------------------


class TestFindCorrelatedProxy:
    def test_picks_lowest_rolling_p_and_persists(self, monkeypatch, store):
        monkeypatch.setattr("engine.cache_long_short_engine.get_provider", lambda: object())
        monkeypatch.setattr(
            "engine.cache_long_short_engine.analyze_pair",
            lambda t, c, p: {"rolling_p": 0.01 if c == "XLK" else 0.10},
        )
        monkeypatch.setattr(
            "engine.cache_long_short_engine._pearson_correlation",
            lambda t, c, lookback_days=90: 0.85,
        )
        proxy, corr = CacheLongShortEngine.find_correlated_proxy("AAPL", candidates=["SPY", "XLK"])
        assert proxy == "XLK"
        assert corr == 0.85
        persisted = store.get_security_proxy("AAPL")
        assert persisted["proxy_ticker"] == "XLK"
        assert persisted["correlation_coefficient"] == 0.85

    def test_skips_candidate_with_no_price_overlap_never_fabricates(self, monkeypatch, store):
        monkeypatch.setattr("engine.cache_long_short_engine.get_provider", lambda: object())
        monkeypatch.setattr(
            "engine.cache_long_short_engine.analyze_pair",
            lambda t, c, p: {"rolling_p": 0.01},
        )
        # No usable price overlap for the only candidate -- must not fall
        # back to a fabricated correlation constant.
        monkeypatch.setattr(
            "engine.cache_long_short_engine._pearson_correlation",
            lambda t, c, lookback_days=90: None,
        )
        proxy, corr = CacheLongShortEngine.find_correlated_proxy("AAPL", candidates=["XLK"])
        assert proxy is None
        assert corr is None

    def test_no_candidate_has_rolling_p_returns_none(self, monkeypatch, store):
        monkeypatch.setattr("engine.cache_long_short_engine.get_provider", lambda: object())
        monkeypatch.setattr(
            "engine.cache_long_short_engine.analyze_pair", lambda t, c, p: {"found": False}
        )
        proxy, corr = CacheLongShortEngine.find_correlated_proxy("AAPL", candidates=["XLK", "SPY"])
        assert proxy is None
        assert corr is None

    def test_skips_candidate_matching_the_ticker_itself(self, monkeypatch, store):
        monkeypatch.setattr("engine.cache_long_short_engine.get_provider", lambda: object())
        calls = []

        def fake_analyze(t, c, p):
            calls.append(c)
            return {"rolling_p": 0.01}

        monkeypatch.setattr("engine.cache_long_short_engine.analyze_pair", fake_analyze)
        monkeypatch.setattr(
            "engine.cache_long_short_engine._pearson_correlation",
            lambda t, c, lookback_days=90: 0.9,
        )
        CacheLongShortEngine.find_correlated_proxy("AAPL", candidates=["AAPL", "XLK"])
        assert "AAPL" not in calls
        assert "XLK" in calls


# ---------------------------------------------------------------------------
# check_correlation_drift
# ---------------------------------------------------------------------------


class TestCheckCorrelationDrift:
    def test_persists_and_returns_fresh_correlation(self, monkeypatch, store):
        monkeypatch.setattr(
            "engine.cache_long_short_engine._pearson_correlation",
            lambda t, c, lookback_days=90: 0.4,
        )
        result = CacheLongShortEngine.check_correlation_drift("AAPL", "XLK")
        assert result == 0.4
        persisted = store.get_security_proxy("AAPL")
        assert persisted["correlation_coefficient"] == 0.4

    def test_returns_none_and_does_not_persist_on_failure(self, monkeypatch, store):
        monkeypatch.setattr(
            "engine.cache_long_short_engine._pearson_correlation",
            lambda t, c, lookback_days=90: None,
        )
        result = CacheLongShortEngine.check_correlation_drift("AAPL", "XLK")
        assert result is None
        assert store.get_security_proxy("AAPL") is None


# ---------------------------------------------------------------------------
# check_wash_sale -- real SQL against a real in-memory store
#
# The rule is about ACQUISITION timing, not about a closed lot's realized
# P&L sign or its close_date. Every case below is a real repro of the
# pre-fix bug (which checked "closed lot, realized loss, close_date within
# 30d" instead): a textbook recent-purchase wash sale went undetected
# (test_blocks_on_open_lot_acquired_within_30_days_before_sale), an old,
# fully-resolved loss with no repurchase since was false-blocked
# (test_allows_when_acquisition_is_more_than_30_days_before_sale), and a
# reacquisition after harvesting -- the other classic trigger -- was never
# checked at all (test_blocks_on_reacquisition_after_closing_a_loss).
# ---------------------------------------------------------------------------


class TestCheckWashSale:
    def test_blocks_on_open_lot_acquired_within_30_days_before_sale(self, store):
        # The textbook wash-sale trigger: a recent purchase, no closed lot
        # involved at all. The pre-fix implementation never looked at
        # acquisitions and returned False here.
        pos_id = store.record_position("AAPL", "long")
        recent = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=25)
        store.record_tax_lot(pos_id, recent, 150.0, 10.0)
        assert CacheLongShortEngine.check_wash_sale("AAPL") is True

    def test_allows_when_acquisition_is_more_than_30_days_before_sale(self, store):
        # Acquired 35 days ago, nothing since -- outside the window either
        # way.
        pos_id = store.record_position("AAPL", "long")
        old_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=35)
        store.record_tax_lot(pos_id, old_date, 150.0, 10.0)
        assert CacheLongShortEngine.check_wash_sale("AAPL") is False

    def test_closed_loss_lot_with_old_acquisition_and_no_repurchase_is_not_blocked(self, store):
        # Pre-fix bug, over-conservative direction: an old, already-resolved
        # loss lot with no repurchase since must NOT block just because it
        # was closed recently -- the close_date/realized_pnl of a past lot
        # is irrelevant to whether a NEW harvest today is a wash sale.
        pos_id = store.record_position("AAPL", "long")
        old_acquisition = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=60)
        lot_id = store.record_tax_lot(pos_id, old_acquisition, 150.0, 10.0)
        store.close_tax_lot(
            lot_id, realized_pnl=-500.0,
            close_date=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=20),
        )
        assert CacheLongShortEngine.check_wash_sale("AAPL") is False

    def test_blocks_on_reacquisition_after_closing_a_loss(self, store):
        # A loss was harvested, then the same ticker was repurchased a few
        # days later, still inside the 30-day window -- the other classic
        # wash-sale trigger, undetectable by close_date/realized_pnl alone.
        pos_id = store.record_position("AAPL", "long")
        old_acquisition = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=40)
        lot_id = store.record_tax_lot(pos_id, old_acquisition, 150.0, 10.0)
        store.close_tax_lot(
            lot_id, realized_pnl=-500.0,
            close_date=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10),
        )
        store.record_tax_lot(
            pos_id, datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=5), 140.0, 10.0
        )
        assert CacheLongShortEngine.check_wash_sale("AAPL") is True

    def test_pnl_direction_of_a_closed_lot_never_affects_the_check(self, store):
        # A closed GAIN lot acquired recently still blocks -- wash-sale
        # eligibility is about acquisition timing, never about whether some
        # other lot's close was a gain or a loss.
        pos_id = store.record_position("AAPL", "long")
        recent = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)
        lot_id = store.record_tax_lot(pos_id, recent, 150.0, 10.0)
        store.close_tax_lot(lot_id, realized_pnl=500.0, close_date=datetime.datetime.now(datetime.timezone.utc))
        assert CacheLongShortEngine.check_wash_sale("AAPL") is True

    def test_allows_ticker_with_no_history(self, store):
        assert CacheLongShortEngine.check_wash_sale("NEWCO") is False

    def test_as_of_param_supports_a_historical_check(self, store):
        # A lot acquired "today" is outside a ±30-day window centered 60
        # days in the future.
        pos_id = store.record_position("AAPL", "long")
        store.record_tax_lot(pos_id, datetime.datetime.now(datetime.timezone.utc), 150.0, 10.0)
        future_check = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=60)
        assert CacheLongShortEngine.check_wash_sale("AAPL", as_of=future_check) is False


# ---------------------------------------------------------------------------
# scan_tlh_opportunities -- real store, mocked prices
# ---------------------------------------------------------------------------


class TestScanTlhOpportunities:
    def test_flags_and_persists_a_real_loss(self, monkeypatch, store):
        pos_id = store.record_position("AAPL", "long")
        lot_id = store.record_tax_lot(pos_id, datetime.datetime.now(datetime.timezone.utc), 150.0, 10.0)

        mock_hstore = type(
            "S", (), {"get_bars": lambda self, ticker, lookback_days=5: _bars([100.0])}
        )()
        monkeypatch.setattr(
            "engine.cache_long_short_engine.HistoricalStore", lambda: mock_hstore
        )
        monkeypatch.setattr("engine.cache_long_short_engine.settings.CACHE_LONG_SHORT_TLH_THRESHOLD_PCT", 0.05)

        opportunities = CacheLongShortEngine.scan_tlh_opportunities()
        assert len(opportunities) == 1
        assert opportunities[0].lot_id == lot_id

        pending = store.get_pending_tlh_lots()
        assert len(pending) == 1
        assert pending[0].lot_id == lot_id
        assert pending[0].unrealized_loss_pct == pytest.approx((100.0 - 150.0) / 150.0)

    def test_ignores_a_gain(self, monkeypatch, store):
        pos_id = store.record_position("AAPL", "long")
        store.record_tax_lot(pos_id, datetime.datetime.now(datetime.timezone.utc), 100.0, 10.0)

        mock_hstore = type(
            "S", (), {"get_bars": lambda self, ticker, lookback_days=5: _bars([150.0])}
        )()
        monkeypatch.setattr(
            "engine.cache_long_short_engine.HistoricalStore", lambda: mock_hstore
        )

        opportunities = CacheLongShortEngine.scan_tlh_opportunities()
        assert opportunities == []
        assert store.get_pending_tlh_lots() == []

    def test_ignores_loss_below_threshold(self, monkeypatch, store):
        pos_id = store.record_position("AAPL", "long")
        store.record_tax_lot(pos_id, datetime.datetime.now(datetime.timezone.utc), 100.0, 10.0)

        # A 1% loss, below the 5% default threshold.
        mock_hstore = type(
            "S", (), {"get_bars": lambda self, ticker, lookback_days=5: _bars([99.0])}
        )()
        monkeypatch.setattr(
            "engine.cache_long_short_engine.HistoricalStore", lambda: mock_hstore
        )
        monkeypatch.setattr("engine.cache_long_short_engine.settings.CACHE_LONG_SHORT_TLH_THRESHOLD_PCT", 0.05)

        assert CacheLongShortEngine.scan_tlh_opportunities() == []

    def test_short_position_loss_direction_is_inverted(self, monkeypatch, store):
        pos_id = store.record_position("AAPL", "short")
        # A short position loses money when price rises above cost basis.
        store.record_tax_lot(pos_id, datetime.datetime.now(datetime.timezone.utc), 100.0, 10.0)

        mock_hstore = type(
            "S", (), {"get_bars": lambda self, ticker, lookback_days=5: _bars([150.0])}
        )()
        monkeypatch.setattr(
            "engine.cache_long_short_engine.HistoricalStore", lambda: mock_hstore
        )
        monkeypatch.setattr("engine.cache_long_short_engine.settings.CACHE_LONG_SHORT_TLH_THRESHOLD_PCT", 0.05)

        opportunities = CacheLongShortEngine.scan_tlh_opportunities()
        assert len(opportunities) == 1

    def test_no_open_lots_returns_empty_without_price_lookup(self, monkeypatch, store):
        calls = []
        mock_hstore = type(
            "S",
            (),
            {"get_bars": lambda self, ticker, lookback_days=5: (calls.append(ticker), _bars([1.0]))[1]},
        )()
        monkeypatch.setattr(
            "engine.cache_long_short_engine.HistoricalStore", lambda: mock_hstore
        )
        assert CacheLongShortEngine.scan_tlh_opportunities() == []
        assert calls == []


# ---------------------------------------------------------------------------
# generate_sell_down_orders
# ---------------------------------------------------------------------------


class TestGenerateSellDownOrders:
    def test_blocked_by_wash_sale(self, store):
        pos_id = store.record_position("AAPL", "long")
        lot_id = store.record_tax_lot(pos_id, datetime.datetime.now(datetime.timezone.utc), 150.0, 10.0)
        store.close_tax_lot(lot_id, realized_pnl=-500.0, close_date=datetime.datetime.now(datetime.timezone.utc))

        result = CacheLongShortEngine.generate_sell_down_orders("AAPL")
        assert result["status"] == "blocked"
        assert "wash sale" in result["reason"].lower()

    def test_blocked_when_no_tax_bank(self, store):
        result = CacheLongShortEngine.generate_sell_down_orders("MSFT")
        assert result["status"] == "blocked"
        assert "tax" in result["reason"].lower()

    def test_approved_sizes_to_tax_bank(self, store):
        # Tax bank is harvested from MSFT, but the sell-down recommendation
        # is for a *different* ticker (GOOGL) -- the wash-sale guardrail is
        # per-ticker, and tax_bank() is a global pool, so these are
        # independent by design.
        pos_id = store.record_position("MSFT", "long")
        lot_id = store.record_tax_lot(pos_id, datetime.datetime.now(datetime.timezone.utc), 150.0, 10.0)
        store.close_tax_lot(lot_id, realized_pnl=-300.0, close_date=datetime.datetime.now(datetime.timezone.utc))

        result = CacheLongShortEngine.generate_sell_down_orders("GOOGL")
        assert result["status"] == "approved"
        assert result["recommended_sell_value"] == pytest.approx(300.0)

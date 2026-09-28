"""
tests/test_paper_marking_and_model_feed.py
==========================================
PaperAccountStore position marking (real marks only, unpriced positions
flagged rather than silently valued) and the paper -> transactions_store
model-feed bridge's eligibility filter.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

import data.paper_account_store as pas
from data.paper_account_store import PaperAccountStore, PaperClosedTrade
from db_config import session_scope
from settings import settings

FUTURE_EXP = (date.today() + timedelta(days=60)).isoformat()
PAST_EXP = (date.today() - timedelta(days=3)).isoformat()


@pytest.fixture
def store():
    return PaperAccountStore(db_url="sqlite:///:memory:")


def _chain(calls=None, puts=None):
    cols = ["strike", "bid", "ask", "lastPrice", "impliedVolatility"]
    return SimpleNamespace(
        calls=pd.DataFrame(calls or [], columns=cols),
        puts=pd.DataFrame(puts or [], columns=cols),
    )


# ---------------------------------------------------------------------------
# Stock marking
# ---------------------------------------------------------------------------


def test_missing_stock_quote_falls_back_to_cost_and_is_flagged(store):
    with patch.object(pas, "_fetch_stock_prices", return_value={}):
        assert store.apply_fill("b1", "AAPL", "buy", 10.0, 150.0) is True
        acct = store.get_account()
        assert store.last_unpriced_symbols == ["AAPL"]
        # Cost basis, never $0.
        assert acct.equity == pytest.approx(acct.cash + 1500.0)

        [pos] = store.get_open_positions()
        assert pos.mark_is_estimated is True
        assert pos.market_value == pytest.approx(1500.0)


def test_real_stock_quote_is_used_and_not_flagged(store):
    with patch.object(pas, "_fetch_stock_prices", return_value={"AAPL": 160.0}):
        store.apply_fill("b1", "AAPL", "buy", 10.0, 150.0)
        [pos] = store.get_open_positions()
        assert store.last_unpriced_symbols == []
        assert pos.mark_is_estimated is False
        assert pos.market_value == pytest.approx(1600.0)
        assert pos.unrealized_pl == pytest.approx(100.0)


@pytest.mark.real_paper_marking
def test_fetch_stock_prices_never_passes_zero_or_nan(monkeypatch):
    """The real seam drops non-positive/non-finite quotes instead of handing
    a $0 price to the marking code (the old `float(q.get("price", 0.0))` bug)."""
    from data.market_data import Quote

    def _q(sym, price):
        return Quote(symbol=sym, price=price, bid=None, ask=None,
                     timestamp=datetime.now(timezone.utc), source="fmp", is_stale=False)

    fake = SimpleNamespace(get_quotes_batch=lambda syms: {
        "AAPL": _q("AAPL", 150.0), "ZERO": _q("ZERO", 0.0), "NAN": _q("NAN", float("nan")),
    })
    monkeypatch.setattr("data.market_data.get_provider", lambda: fake)
    assert pas._fetch_stock_prices(["AAPL", "ZERO", "NAN", "GONE"]) == {"AAPL": 150.0}


def test_session_not_held_open_during_quote_fetch(store):
    """Pricing (network I/O) runs after the DB session closes, so a slow quote
    can't pin a SQLite transaction open."""
    store.apply_fill("b1", "AAPL", "buy", 1.0, 100.0)

    def _probe(symbols):
        # A write from inside the fetch would deadlock/raise if get_account()
        # still held its session open on a single-connection :memory: DB.
        with session_scope(store.Session) as s:
            s.execute(pas.text("SELECT 1"))
        return {"AAPL": 101.0}

    with patch.object(pas, "_fetch_stock_prices", side_effect=_probe):
        assert store.get_account().equity > 0


# ---------------------------------------------------------------------------
# Option marking -- real chain data only, never a hardcoded volatility
# ---------------------------------------------------------------------------


def _open_long_call(store, strike=150.0, exp=FUTURE_EXP, price=500.0):
    sym = f"AAPL {exp} ${strike:.2f} CALL"
    assert store.apply_fill("oc1", sym, "buy", 1.0, price) is True
    return sym


def test_option_marked_from_chain_mid(store):
    sym = _open_long_call(store)
    chain = _chain(calls=[{"strike": 150.0, "bid": 6.0, "ask": 6.4, "lastPrice": 9.0, "impliedVolatility": 0.2}])
    with patch.object(pas, "_fetch_stock_prices", return_value={"AAPL": 152.0}), \
         patch.object(pas, "_fetch_option_chain", return_value=chain):
        [pos] = store.get_open_positions()
    assert pos.symbol == sym
    assert pos.mark_is_estimated is False
    assert pos.market_value == pytest.approx(620.0)  # 6.20 mid x 100


def test_option_marked_from_live_iv_when_no_quote(store):
    _open_long_call(store)
    from data.option_symbols import black_scholes_price

    chain = _chain(calls=[{"strike": 150.0, "bid": 0.0, "ask": 0.0, "lastPrice": 1.0, "impliedVolatility": 0.18}])
    with patch.object(pas, "_fetch_stock_prices", return_value={"AAPL": 152.0}), \
         patch.object(pas, "_fetch_option_chain", return_value=chain):
        [pos] = store.get_open_positions()
    assert pos.mark_is_estimated is False
    # Uses the contract's own IV (0.18), not a hardcoded 0.30.
    t_years_upper = 61 / 365.0
    hi = black_scholes_price(152.0, 150.0, t_years_upper, 0.18, "call") * 100
    at_30 = black_scholes_price(152.0, 150.0, t_years_upper, 0.30, "call") * 100
    assert pos.market_value <= hi + 1.0
    assert pos.market_value < at_30 - 50.0


def test_expired_option_marked_at_intrinsic(store):
    _open_long_call(store, exp=PAST_EXP)
    with patch.object(pas, "_fetch_stock_prices", return_value={"AAPL": 158.0}), \
         patch.object(pas, "_fetch_option_chain", side_effect=AssertionError("no chain for expired")):
        [pos] = store.get_open_positions()
    assert pos.mark_is_estimated is False
    assert pos.market_value == pytest.approx(800.0)  # (158-150) x 100


def test_option_without_chain_row_is_flagged_at_cost(store):
    _open_long_call(store, price=500.0)
    with patch.object(pas, "_fetch_stock_prices", return_value={"AAPL": 152.0}), \
         patch.object(pas, "_fetch_option_chain", return_value=None):
        [pos] = store.get_open_positions()
    assert pos.mark_is_estimated is True
    assert pos.market_value == pytest.approx(500.0)
    assert pos.unrealized_pl == pytest.approx(0.0)


@pytest.mark.real_paper_marking
def test_option_chain_is_cached(monkeypatch):
    calls = []

    class _Prov:
        def fetch_options_chain(self, sym, exp):
            calls.append((sym, exp))
            return _chain()

    monkeypatch.setattr("data.market_data.get_options_provider", lambda: _Prov())
    monkeypatch.setattr(settings, "PAPER_OPTION_MARK_CACHE_SECONDS", 60.0)
    pas._OPTION_CHAIN_CACHE.clear()
    pas._fetch_option_chain("AAPL", FUTURE_EXP)
    pas._fetch_option_chain("AAPL", FUTURE_EXP)
    assert calls == [("AAPL", FUTURE_EXP)]


# (Step 4b, options desk archive: test_exit_evaluation_skips_groups_with_
# unpriced_legs, which exercised execution.options_paper_executor.py's
# auto-exit engine, moved to tests/test_options_paper_executor.py -- that
# whole module is archived to legacy/ with it. The marking tests above --
# what this file is actually about -- are unaffected.)


# ---------------------------------------------------------------------------
# Model feed: only signal-driven equity trades are bridged
# ---------------------------------------------------------------------------


def _bridged_rows(db_url):
    import transactions_store

    return transactions_store.TransactionsStore(db_url=db_url).closed_trades_df()


def _closed(store):
    with session_scope(store.Session) as s:
        return [(r.symbol, r.strategy_id, r.bridge_status, r.bridge_error) for r in s.query(PaperClosedTrade).all()]


def test_bridge_is_on_by_default_and_feeds_signal_equity_trades(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'feed.db'}"
    store = PaperAccountStore(db_url=db_url)
    store.apply_fill("b", "MSFT", "buy", 5.0, 400.0, strategy_id="momentum-pilot")
    store.apply_fill("s", "MSFT", "sell", 5.0, 420.0, strategy_id="momentum-pilot")

    df = _bridged_rows(db_url)
    assert len(df) == 1
    assert df.iloc[0]["strategy"] == "momentum-pilot"
    assert _closed(store)[0][2] == "bridged"


@pytest.mark.parametrize("strategy_id", ["Manual Trade", "Delta Hedge", "untagged", "manual trade"])
def test_bridge_excludes_non_model_strategies(tmp_path, strategy_id):
    db_url = f"sqlite:///{tmp_path / 'excl.db'}"
    store = PaperAccountStore(db_url=db_url)
    store.apply_fill("b", "SPY", "buy", 5.0, 500.0, strategy_id=strategy_id)
    store.apply_fill("s", "SPY", "sell", 5.0, 490.0, strategy_id=strategy_id)

    assert _bridged_rows(db_url).empty
    [(sym, sid, status, err)] = _closed(store)
    assert status == "excluded"
    assert "not a model-driven strategy" in err
    metrics = store.get_bridge_completeness_metrics()
    assert metrics["excluded_count"] == 1
    assert metrics["attempted_count"] == 0


def test_bridge_excludes_option_contracts(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'opt.db'}"
    store = PaperAccountStore(db_url=db_url)
    sym = f"AAPL {FUTURE_EXP} $150.00 CALL"
    store.apply_fill("b", sym, "buy", 1.0, 500.0, strategy_id="iron-condor")
    store.apply_fill("s", sym, "sell", 1.0, 650.0, strategy_id="iron-condor")

    assert _bridged_rows(db_url).empty
    [(_, _, status, err)] = _closed(store)
    assert status == "excluded"
    assert "option contract" in err


def test_bridge_exclusion_list_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_EXCLUDED_STRATEGIES", ["Manual Trade"])
    db_url = f"sqlite:///{tmp_path / 'cfg.db'}"
    store = PaperAccountStore(db_url=db_url)
    store.apply_fill("b", "SPY", "buy", 1.0, 500.0, strategy_id="Delta Hedge")
    store.apply_fill("s", "SPY", "sell", 1.0, 505.0, strategy_id="Delta Hedge")
    assert len(_bridged_rows(db_url)) == 1

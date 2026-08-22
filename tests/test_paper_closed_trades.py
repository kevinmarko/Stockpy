import pytest
from data.paper_account_store import PaperAccountStore

@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_account.db"
    return PaperAccountStore(db_url=f"sqlite:///{db_path}")

def test_paper_closed_trade_long_pnl(store):
    store.reset_account()
    store.apply_fill("id-1", "SPY", "BUY", 10.0, 100.0)
    store.apply_fill("id-2", "SPY", "SELL", 10.0, 110.0)
    with store.engine.begin() as conn:
        res = conn.execute(__import__("sqlalchemy").text("SELECT realized_pnl FROM paper_closed_trades")).fetchall()
        assert res[0][0] == 100.0  # (110 - 100) * 10

def test_paper_closed_trade_short_pnl(store):
    store.reset_account()
    store.apply_fill("id-1", "SPY", "SELL", 10.0, 100.0, allow_short=True)
    store.apply_fill("id-2", "SPY", "BUY", 10.0, 90.0, allow_short=True)
    with store.engine.begin() as conn:
        res = conn.execute(__import__("sqlalchemy").text("SELECT realized_pnl FROM paper_closed_trades")).fetchall()
        assert res[0][0] == 100.0  # (100 - 90) * 10

def test_paper_closed_trade_option_multiplier(store):
    store.reset_account()
    store.apply_fill("id-1", "SPY 2026-01-01 $100 CALL", "BUY", 1.0, 5.0)
    store.apply_fill("id-2", "SPY 2026-01-01 $100 CALL", "SELL", 1.0, 6.0)
    with store.engine.begin() as conn:
        res = conn.execute(__import__("sqlalchemy").text("SELECT realized_pnl FROM paper_closed_trades")).fetchall()
        assert res[0][0] == 100.0  # (6 - 5) * 1 * 100

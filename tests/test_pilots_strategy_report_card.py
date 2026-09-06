import pytest
from unittest.mock import patch, MagicMock

from pilots.strategy_report_card import (
    _normalize_strategy_id,
    strategy_report_card_rows,
    MIN_TRADES_FOR_VERDICT,
)
from pilots.catalog import Pilot, list_pilots


def test_normalize_strategy_id():
    # legacy-string DB row ("Copula Stat Arb")
    assert _normalize_strategy_id("Copula Stat Arb") == "copula-stat-arb"
    # "Follow:trend-following" row
    assert _normalize_strategy_id("Follow:trend-following") == "trend-following"
    # standard
    assert _normalize_strategy_id("trend-following") == "trend-following"


@patch("pilots.strategy_report_card.ValidationHistoryStore")
@patch("pilots.strategy_report_card.PaperAccountStore")
def test_strategy_report_card_rows(mock_paper, mock_validation):
    # Setup mock validation data
    mock_val_store = MagicMock()
    mock_validation.return_value = mock_val_store
    mock_val_store.get_latest_per_strategy.return_value = {
        "timeseries_momentum": {
            "deployable": True,
            "pbo": 0.05,
            "dsr": 1.2,
            "sharpe": 1.5,
            "max_drawdown": 10.0,
            "n_trials": 1000,
            "is_options_selling": False,
            "stress_gate_passed": True,
            "report_date": "2026-09-06",
        }
    }

    # Setup mock paper trades
    mock_paper_store = MagicMock()
    mock_paper.return_value = mock_paper_store
    
    # 1. real Pilot with both sides populated: "trend-following"
    # Needs >= MIN_TRADES_FOR_VERDICT (10)
    trend_trades = [
        {"strategy_id": "trend-following", "realized_pnl": 100.0, "realized_pnl_pct": 0.05, "exit_ts": "2026-01-01"}
        for _ in range(12)
    ]
    
    # 2. validation_strategy_id=None Pilot (e.g., "earnings-crush")
    # Will check if "earnings-crush" is a pilot (it is, per catalog)
    
    # 3. zero-paper-trade Pilot (any pilot not in trades)
    
    # 4. non-Pilot bucket ("Delta Hedge")
    # Needs >= 10 trades to show populated actual side
    delta_hedge_trades = [
        {"strategy_id": "Delta Hedge", "realized_pnl": 50.0, "realized_pnl_pct": 0.01, "exit_ts": "2026-01-01"}
        for _ in range(11)
    ]
    
    # 5. legacy-string DB row ("Copula Stat Arb") -> normalizes to "copula-stat-arb"
    copula_trades = [
        {"strategy_id": "Copula Stat Arb", "realized_pnl": 50.0, "realized_pnl_pct": 0.05, "exit_ts": "2026-01-01"}
        for _ in range(10)
    ]
    
    # 6. "Follow:trend-following" row -> normalizes to "trend-following" (add to trend trades)
    trend_trades.append({"strategy_id": "Follow:trend-following", "realized_pnl": 200.0, "realized_pnl_pct": 0.10, "exit_ts": "2026-01-02"})
    
    # 7. exact n=9 vs n=10 honesty-floor boundary
    # Pilot with 9 trades
    pilot_9 = [
        {"strategy_id": "mean-reversion", "realized_pnl": 10.0, "realized_pnl_pct": 0.02, "exit_ts": "2026-01-01"}
        for _ in range(9)
    ]
    # Pilot with 10 trades
    pilot_10 = [
        {"strategy_id": "options-flow-sentiment", "realized_pnl": 10.0, "realized_pnl_pct": 0.02, "exit_ts": "2026-01-01"}
        for _ in range(10)
    ]

    mock_paper_store.get_full_closed_trades.return_value = (
        trend_trades + delta_hedge_trades + copula_trades + pilot_9 + pilot_10
    )

    rows = strategy_report_card_rows()
    rows_by_id = {r["pilot_id"]: r for r in rows}

    # 1. Real Pilot with both sides populated ("trend-following")
    # (Assuming trend-following exists in catalog, which it does)
    assert "trend-following" in rows_by_id
    tf = rows_by_id["trend-following"]
    assert tf["is_pilot"] is True
    assert tf["predicted"]["sharpe"] == 1.5
    assert tf["actual"]["trade_count"] == 13 # 12 + 1 from Follow:trend-following
    assert tf["actual"]["total_realized_pnl_usd"] == 1400.0

    # 2. validation_strategy_id=None Pilot
    assert "earnings-crush" in rows_by_id
    ec = rows_by_id["earnings-crush"]
    assert ec["predicted"]["reason"] == "no validated backtest for this pilot"
    assert ec["predicted"]["sharpe"] is None

    # 3. zero-paper-trade Pilot
    # Let's pick a pilot we didn't add trades for, e.g., "sector-quality-rank"
    # Actually just check any pilot that has trade_count = 0
    zero_pilots = [r for r in rows if r["actual"]["trade_count"] == 0]
    assert len(zero_pilots) > 0
    zp = zero_pilots[0]
    assert zp["actual"]["reason"] == "insufficient sample (n=0)"

    # 4. non-Pilot bucket ("Delta Hedge")
    assert "Delta Hedge" in rows_by_id
    dh = rows_by_id["Delta Hedge"]
    assert dh["is_pilot"] is False
    assert dh["actual"]["trade_count"] == 11
    assert dh["predicted"]["reason"] == "non-pilot bucket"

    # 7. exact n=9 vs n=10 honesty-floor boundary
    assert "mean-reversion" in rows_by_id
    mr = rows_by_id["mean-reversion"]
    assert mr["actual"]["trade_count"] == 9
    assert mr["actual"]["reason"] == "insufficient sample (n=9)"
    assert mr["actual"]["win_rate"] is None

    assert "options-flow-sentiment" in rows_by_id
    ofs = rows_by_id["options-flow-sentiment"]
    assert ofs["actual"]["trade_count"] == 10
    assert ofs["actual"]["reason"] is None
    assert ofs["actual"]["win_rate"] == 1.0 # 10 positive trades

    # 5. legacy-string DB row ("Copula Stat Arb") resolves to "copula-stat-arb"
    assert "copula-stat-arb" in rows_by_id
    csa = rows_by_id["copula-stat-arb"]
    assert csa["actual"]["trade_count"] == 10
    assert csa["actual"]["total_realized_pnl_usd"] == 500.0

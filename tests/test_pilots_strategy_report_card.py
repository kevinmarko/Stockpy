import pytest
from unittest.mock import patch, MagicMock

from pilots.strategy_report_card import (
    _normalize_strategy_id,
    strategy_report_card_rows,
    MIN_TRADES_FOR_VERDICT,
    LEGACY_STRATEGY_ID_ALIASES,
)
from pilots.catalog import Pilot, list_pilots


def test_normalize_strategy_id():
    # legacy-string DB row ("Copula Stat Arb")
    assert _normalize_strategy_id("Copula Stat Arb") == "copula-stat-arb"
    # "Follow:trend-following" row
    assert _normalize_strategy_id("Follow:trend-following") == "trend-following"
    # standard
    assert _normalize_strategy_id("trend-following") == "trend-following"


def test_all_legacy_aliases_are_present_and_correct():
    """Every one of the 10 documented LEGACY_STRATEGY_ID_ALIASES entries must
    map to the exact, real canonical pilots.catalog id -- not a partial set,
    not a plausible-looking-but-wrong misspelling. A wrong value here would
    silently ORPHAN historical paper-trade data into its own made-up
    non-Pilot bucket instead of joining the real Pilot's row (this is exactly
    how "Dispersion Arbitrage" -> "dispersion-arbitrage" and "0DTE Momentum
    Breakout" -> "0dte-momentum-breakout" were previously wrong: neither
    value was a real pilots.catalog id)."""
    expected = {
        "Dispersion Arbitrage": "dispersion-trading",
        "Copula Stat Arb": "copula-stat-arb",
        "0DTE Momentum Breakout": "zero-dte-momentum-breakout",
        "Earnings Crush": "earnings-crush",
        "Put Credit Spread": "put-credit-spread",
        "Call Credit Spread": "call-credit-spread",
        "Iron Condor": "iron-condor",
        "Call Debit Spread": "call-debit-spread",
        "Put Debit Spread": "put-debit-spread",
        "Covered Call": "covered-call",
    }
    assert LEGACY_STRATEGY_ID_ALIASES == expected

    # Belt-and-suspenders: every alias VALUE must resolve to a real,
    # currently-registered pilots.catalog Pilot id -- catches a future
    # catalog rename that silently orphans this alias map too.
    real_pilot_ids = {p.id for p in list_pilots()}
    for legacy, canonical in LEGACY_STRATEGY_ID_ALIASES.items():
        assert canonical in real_pilot_ids, (
            f"LEGACY_STRATEGY_ID_ALIASES[{legacy!r}] = {canonical!r} does not "
            "match any real pilots.catalog Pilot id -- this would silently "
            "orphan historical paper-trade data into its own bucket."
        )


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

    # 8. two more legacy-string DB rows that must resolve through the alias
    # map into their REAL canonical Pilot bucket, not an orphaned bucket
    # keyed by the stale/wrong alias value (the exact class of bug this
    # module previously shipped for both of these two entries).
    dispersion_legacy_trades = [
        {"strategy_id": "Dispersion Arbitrage", "realized_pnl": 25.0, "realized_pnl_pct": 0.03, "exit_ts": "2026-01-01"}
        for _ in range(10)
    ]
    zero_dte_legacy_trades = [
        {"strategy_id": "0DTE Momentum Breakout", "realized_pnl": 15.0, "realized_pnl_pct": 0.02, "exit_ts": "2026-01-01"}
        for _ in range(10)
    ]

    mock_paper_store.get_full_closed_trades.return_value = (
        trend_trades + delta_hedge_trades + copula_trades + pilot_9 + pilot_10
        + dispersion_legacy_trades + zero_dte_legacy_trades
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

    # 8. "Dispersion Arbitrage" (legacy free-text label) must resolve to the
    # REAL canonical "dispersion-trading" Pilot bucket -- not an orphaned
    # "Dispersion Arbitrage"/"dispersion-arbitrage" non-Pilot row.
    assert "Dispersion Arbitrage" not in rows_by_id
    assert "dispersion-arbitrage" not in rows_by_id
    assert "dispersion-trading" in rows_by_id
    disp = rows_by_id["dispersion-trading"]
    assert disp["is_pilot"] is True
    assert disp["actual"]["trade_count"] == 10
    assert disp["actual"]["total_realized_pnl_usd"] == 250.0

    # "0DTE Momentum Breakout" (legacy free-text label) must resolve to the
    # REAL canonical "zero-dte-momentum-breakout" Pilot bucket -- not an
    # orphaned "0DTE Momentum Breakout"/"0dte-momentum-breakout" non-Pilot row.
    assert "0DTE Momentum Breakout" not in rows_by_id
    assert "0dte-momentum-breakout" not in rows_by_id
    assert "zero-dte-momentum-breakout" in rows_by_id
    zdte = rows_by_id["zero-dte-momentum-breakout"]
    assert zdte["is_pilot"] is True
    assert zdte["actual"]["trade_count"] == 10
    assert zdte["actual"]["total_realized_pnl_usd"] == 150.0

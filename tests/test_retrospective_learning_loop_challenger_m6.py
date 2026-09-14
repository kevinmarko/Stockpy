"""tests/test_retrospective_learning_loop_challenger_m6.py
=========================================================
Milestone 6 Challenger 1 Adversarial Coverage Hardening Suite.

Tasks Challenged:
1. Pipeline Integrity across fill types (apply_fill, apply_multi_leg_fill, apply_roll_fill)
   with missing, corrupt, and rich signal contexts, and forward-only capture verification.
2. Stress test multi-trade excursion calculation: isolated in-memory stores prevent 100%
   of portfolio collisions under overlapping / identical timestamps and varied holding durations.
3. Challenge cohort isolation under extreme stress: 1,000 extreme/pathological manual trades
   injected alongside automated trades, asserting automated cohort invariance to 10^-9 precision.
"""

from __future__ import annotations

import math
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest

from data.paper_account_store import (
    OrderStatus,
    PaperAccountStore,
    PaperClosedTrade,
    PaperEntrySnapshot,
    PaperPosition,
    session_scope,
)
from evaluation_engine import EvaluationEngine
from pilots.retrospective_composer import RetrospectiveComposer
from pilots.retrospective_insights import (
    _compute_cohort_metrics,
    generate_batch_retrospective_insights,
)
from settings import settings
from transactions_store import TransactionsStore

FORBIDDEN_ROOT_KEYS = {
    "win_rate",
    "total_win_rate",
    "blended_win_rate",
    "aggregate_win_rate",
    "overall_win_rate",
    "total_pnl",
    "blended_pnl",
    "aggregate_pnl",
    "realized_pnl",
    "total_realized_pnl",
    "net_pnl",
    "profit_factor",
    "blended_profit_factor",
    "aggregate_profit_factor",
    "sharpe",
    "blended_sharpe",
    "aggregate_sharpe",
    "edge_ratio",
    "mean_edge_ratio",
    "holding_period_days",
    "mean_holding_period_days",
    "trades",
    "total_trades",
    "trade_count",
}


@pytest.fixture
def isolated_db_url(tmp_path) -> str:
    """Provide isolated SQLite database per test."""
    db_file = tmp_path / f"test_challenger_m6_{uuid.uuid4().hex}.db"
    return f"sqlite:///{db_file}"


@pytest.fixture
def paper_store(isolated_db_url: str) -> PaperAccountStore:
    """Isolated PaperAccountStore instance."""
    return PaperAccountStore(db_url=isolated_db_url)


@pytest.fixture
def transactions_store(isolated_db_url: str) -> TransactionsStore:
    """Isolated TransactionsStore instance."""
    return TransactionsStore(db_url=isolated_db_url)


@pytest.fixture
def evaluation_engine() -> EvaluationEngine:
    """EvaluationEngine instance."""
    return EvaluationEngine()


class _NoOpHistoricalStore:
    """Offline-safe stand-in for HistoricalStore -- a real one falls through
    to a live network fetch on every cache-miss symbol (correct production
    behavior, see RetrospectiveComposer._evaluate_trade_excursion), which
    would make tests that don't explicitly inject their own `data_provider`
    silently depend on outbound network access."""

    def get_bars(self, symbol, lookback_days=504, **kwargs):
        return pd.DataFrame()


@pytest.fixture
def composer(
    paper_store: PaperAccountStore,
    transactions_store: TransactionsStore,
    evaluation_engine: EvaluationEngine,
    isolated_db_url: str,
) -> RetrospectiveComposer:
    """Wired RetrospectiveComposer instance."""
    return RetrospectiveComposer(
        paper_store=paper_store,
        transactions_store=transactions_store,
        evaluation_engine=evaluation_engine,
        historical_store=_NoOpHistoricalStore(),
        db_url=isolated_db_url,
    )


# =============================================================================
# TASK 1: End-to-End Data Pipeline Integrity Across Fill Types
# =============================================================================

class TestPipelineIntegrityAcrossFillTypes:
    """Verify data pipeline integrity across apply_fill, apply_multi_leg_fill, and apply_roll_fill."""

    def test_plain_fills_refuse_snapshot_across_all_methods(self, paper_store: PaperAccountStore, composer: RetrospectiveComposer):
        """Plain/legacy fills lacking decision context MUST NEVER capture snapshots."""
        # 1. Plain apply_fill (single-leg)
        assert paper_store.apply_fill("ord_p1", "AAPL", "buy", 10.0, 150.0)
        with session_scope(paper_store.Session) as session:
            pos = session.query(PaperPosition).filter_by(symbol="AAPL").first()
            assert pos is not None
            assert pos.entry_snapshot_id is None, "Plain apply_fill must NOT generate entry_snapshot_id"

        # Close plain single-leg
        assert paper_store.apply_fill("ord_p2", "AAPL", "sell", 10.0, 155.0)
        with session_scope(paper_store.Session) as session:
            closed = session.query(PaperClosedTrade).filter_by(symbol="AAPL").order_by(PaperClosedTrade.trade_id.desc()).first()
            assert closed is not None
            assert closed.entry_snapshot_id is None, "Closed trade from plain fill must have entry_snapshot_id == None"

            # Retrospective composer inspection
            rec = composer.compose_trade_retrospective(closed.trade_id)
            assert rec is not None
            assert rec["snapshot"]["captured"] is False
            assert rec["snapshot"]["decision_context_status"] == "not_captured"
            assert rec["snapshot"]["provenance"] == "unknown"

        # 2. Plain apply_multi_leg_fill
        legs = [
            {"symbol": "MSFT 260320C00400000", "side": "buy", "qty": 1, "fill_price": 5.0},
            {"symbol": "MSFT 260320C00410000", "side": "sell", "qty": 1, "fill_price": 2.0},
        ]
        assert paper_store.apply_multi_leg_fill(
            client_order_id="ord_ml_plain",
            symbol="MSFT",
            strategy_name="bull_call_spread",
            contracts=1,
            legs=legs,
            net_cash_impact=-300.0,
            commission_and_fees=1.30,
        )
        with session_scope(paper_store.Session) as session:
            pos1 = session.query(PaperPosition).filter_by(symbol="MSFT 260320C00400000").first()
            pos2 = session.query(PaperPosition).filter_by(symbol="MSFT 260320C00410000").first()
            assert pos1 is not None and pos1.entry_snapshot_id is None
            assert pos2 is not None and pos2.entry_snapshot_id is None

        # 3. Plain apply_roll_fill
        close_legs = [
            {"symbol": "MSFT 260320C00400000", "side": "sell", "qty": 1, "fill_price": 6.0},
        ]
        open_legs = [
            {"symbol": "MSFT 260417C00420000", "side": "buy", "qty": 1, "fill_price": 4.0},
        ]
        assert paper_store.apply_roll_fill(
            client_order_id="ord_roll_plain",
            symbol="MSFT",
            close_legs=close_legs,
            open_legs=open_legs,
            net_cash_impact=200.0,
            contracts=1,
        )
        with session_scope(paper_store.Session) as session:
            # Closed leg trade record
            closed_leg = session.query(PaperClosedTrade).filter_by(symbol="MSFT 260320C00400000").first()
            assert closed_leg is not None
            assert closed_leg.entry_snapshot_id is None

            # New rolled position
            new_pos = session.query(PaperPosition).filter_by(symbol="MSFT 260417C00420000").first()
            assert new_pos is not None
            assert new_pos.entry_snapshot_id is None

    def test_rich_context_forward_only_across_all_fill_types(
        self, paper_store: PaperAccountStore, composer: RetrospectiveComposer
    ):
        """Rich decision context must be captured forward-only and linked faithfully."""
        # 1. Single-leg fill with rich context
        assert paper_store.apply_fill(
            client_order_id="ord_rich_1",
            symbol="NVDA",
            side="buy",
            qty=10.0,
            fill_price=120.0,
            strategy_id="momentum_breakout",
            pilot_id="pilot_alpha",
            provenance="signal_driven",
            conviction=0.89,
            macro_regime="BULL_EXPANSION",
            signal_score=2.45,
            raw_forecast=0.065,
            forecast_model="lightgbm_v3",
            key_indicators_json='{"rsi": 68.2, "vol_z": 1.8}',
            decision_rationale="High volume breakout across resistance",
        )

        with session_scope(paper_store.Session) as session:
            pos = session.query(PaperPosition).filter_by(symbol="NVDA").first()
            assert pos is not None
            snap_id = pos.entry_snapshot_id
            assert snap_id is not None
            assert snap_id.startswith("snap_")

            snap = session.query(PaperEntrySnapshot).filter_by(snapshot_id=snap_id).first()
            assert snap is not None
            assert snap.symbol == "NVDA"
            assert snap.strategy_id == "momentum_breakout"
            assert snap.pilot_id == "pilot_alpha"
            assert snap.provenance == "signal_driven"
            assert math.isclose(snap.conviction, 0.89, abs_tol=1e-6)
            assert snap.macro_regime == "BULL_EXPANSION"
            assert math.isclose(snap.signal_score, 2.45, abs_tol=1e-6)
            assert math.isclose(snap.raw_forecast, 0.065, abs_tol=1e-6)
            assert snap.forecast_model == "lightgbm_v3"
            assert '{"rsi": 68.2, "vol_z": 1.8}' in snap.key_indicators_json
            assert "High volume breakout" in snap.decision_rationale

        # Close position with matching strategy_id
        assert paper_store.apply_fill("ord_rich_1_close", "NVDA", "sell", 10.0, 135.0, strategy_id="momentum_breakout")
        with session_scope(paper_store.Session) as session:
            closed = session.query(PaperClosedTrade).filter_by(symbol="NVDA").first()
            assert closed is not None
            assert closed.entry_snapshot_id == snap_id
            closed_trade_id = closed.trade_id

            # Verify snapshot updated with trade_id
            snap = session.query(PaperEntrySnapshot).filter_by(snapshot_id=snap_id).first()
            assert snap.trade_id == str(closed.trade_id)

        # Retrospective composition check
        rec = composer.compose_trade_retrospective(closed_trade_id)
        assert rec is not None
        assert rec["snapshot"]["captured"] is True
        assert rec["snapshot"]["decision_context_status"] == "captured"
        assert rec["snapshot"]["provenance"] == "signal_driven"
        assert math.isclose(rec["snapshot"]["conviction"], 0.89, abs_tol=1e-6)
        assert rec["snapshot"]["macro_regime"] == "BULL_EXPANSION"

        # 2. Multi-leg fill with rich context
        legs = [
            {"symbol": "SPY 260417C00510000", "side": "buy", "qty": 2, "fill_price": 6.5},
            {"symbol": "SPY 260417C00520000", "side": "sell", "qty": 2, "fill_price": 2.5},
        ]
        assert paper_store.apply_multi_leg_fill(
            client_order_id="ord_ml_rich",
            symbol="SPY",
            strategy_name="bull_call_spread",
            contracts=2,
            legs=legs,
            net_cash_impact=-800.0,
            commission_and_fees=2.60,
            strategy_id="options_flow",
            pilot_id="pilot_options",
            provenance="signal_driven",
            conviction=0.78,
            macro_regime="NEUTRAL",
            signal_score=1.85,
            raw_forecast=0.032,
        )
        with session_scope(paper_store.Session) as session:
            pos_leg1 = session.query(PaperPosition).filter_by(symbol="SPY 260417C00510000").first()
            pos_leg2 = session.query(PaperPosition).filter_by(symbol="SPY 260417C00520000").first()
            assert pos_leg1 is not None and pos_leg1.entry_snapshot_id is not None
            assert pos_leg2 is not None and pos_leg2.entry_snapshot_id is not None
            assert pos_leg1.entry_snapshot_id != pos_leg2.entry_snapshot_id
            pos_leg1_snap_id = str(pos_leg1.entry_snapshot_id)

            snap1 = session.query(PaperEntrySnapshot).filter_by(snapshot_id=pos_leg1.entry_snapshot_id).first()
            assert snap1 is not None
            assert snap1.symbol == "SPY 260417C00510000"
            assert math.isclose(snap1.conviction, 0.78, abs_tol=1e-6)

        # 3. Roll fill with rich context
        close_legs = [
            {"symbol": "SPY 260417C00510000", "side": "sell", "qty": 2, "fill_price": 8.0},
        ]
        open_legs = [
            {"symbol": "SPY 260515C00530000", "side": "buy", "qty": 2, "fill_price": 5.0},
        ]
        assert paper_store.apply_roll_fill(
            client_order_id="ord_roll_rich",
            symbol="SPY",
            close_legs=close_legs,
            open_legs=open_legs,
            net_cash_impact=600.0,
            contracts=2,
            strategy_id="options_flow",
            pilot_id="pilot_options",
            provenance="signal_driven",
            conviction=0.82,
            macro_regime="BULL_TREND",
        )
        with session_scope(paper_store.Session) as session:
            # Check closed leg trade
            closed_roll_leg = session.query(PaperClosedTrade).filter_by(symbol="SPY 260417C00510000").first()
            assert closed_roll_leg is not None
            assert closed_roll_leg.entry_snapshot_id == pos_leg1_snap_id

            # Check new opened leg
            new_leg = session.query(PaperPosition).filter_by(symbol="SPY 260515C00530000").first()
            assert new_leg is not None
            assert new_leg.entry_snapshot_id is not None
            snap_new = session.query(PaperEntrySnapshot).filter_by(snapshot_id=new_leg.entry_snapshot_id).first()
            assert snap_new is not None
            assert math.isclose(snap_new.conviction, 0.82, abs_tol=1e-6)

    def test_adversarial_context_and_anti_fabrication(self, paper_store: PaperAccountStore):
        """Adversarial contexts: manual trades must strip conviction/forecast; invalid provenance normalized."""
        # 1. Manual trade attempting to fabricate model conviction and forecast
        assert paper_store.apply_fill(
            client_order_id="ord_adv_manual",
            symbol="TSLA",
            side="buy",
            qty=5.0,
            fill_price=200.0,
            strategy_id="Manual Trade",
            provenance="manual",
            conviction=0.99,  # Hostile: trying to inject fake conviction on a manual trade!
            raw_forecast=0.08,  # Hostile: trying to inject fake forecast!
            signal_score=3.5,  # Hostile!
            macro_regime="CHAOTIC",
        )
        with session_scope(paper_store.Session) as session:
            pos = session.query(PaperPosition).filter_by(symbol="TSLA").first()
            assert pos is not None
            assert pos.entry_snapshot_id is not None

            snap = session.query(PaperEntrySnapshot).filter_by(snapshot_id=pos.entry_snapshot_id).first()
            assert snap is not None
            assert snap.provenance == "manual"
            # ANTI-FABRICATION INVARIANT: Model metrics MUST BE None!
            assert snap.conviction is None, "Manual trade must NOT retain conviction"
            assert snap.raw_forecast is None, "Manual trade must NOT retain raw_forecast"
            assert snap.signal_score is None, "Manual trade must NOT retain signal_score"

        # 2. Corrupt / invalid provenance strings
        assert paper_store.apply_fill(
            client_order_id="ord_corrupt_prov",
            symbol="AMD",
            side="buy",
            qty=10.0,
            fill_price=100.0,
            provenance="MALICIOUS_INJECTION_SQL'--",
            conviction=0.5,
        )
        with session_scope(paper_store.Session) as session:
            pos_amd = session.query(PaperPosition).filter_by(symbol="AMD").first()
            assert pos_amd is not None
            snap_amd = session.query(PaperEntrySnapshot).filter_by(snapshot_id=pos_amd.entry_snapshot_id).first()
            assert snap_amd is not None
            assert snap_amd.provenance == "unknown", "Unrecognized provenance MUST normalize to 'unknown'"

    def test_lifecycle_position_flip_through_zero_maintains_snapshot_integrity(self, paper_store: PaperAccountStore):
        """Flipping a position through zero must close the old side with Snapshot A and open the new side with Snapshot B."""
        # 1. Open long with Snapshot A
        assert paper_store.apply_fill(
            client_order_id="ord_flip_open",
            symbol="META",
            side="buy",
            qty=10.0,
            fill_price=500.0,
            provenance="signal_driven",
            conviction=0.75,
        )
        with session_scope(paper_store.Session) as session:
            pos = session.query(PaperPosition).filter_by(symbol="META").first()
            snap_a_id = pos.entry_snapshot_id
            assert snap_a_id is not None

        # 2. Sell 25 shares (10 to close long, 15 to open short) with Snapshot B
        assert paper_store.apply_fill(
            client_order_id="ord_flip_close_open",
            symbol="META",
            side="sell",
            qty=25.0,
            fill_price=510.0,
            allow_short=True,
            provenance="signal_driven",
            conviction=0.92,
        )
        with session_scope(paper_store.Session) as session:
            # Closed long trade must point to Snapshot A
            closed_long = session.query(PaperClosedTrade).filter_by(symbol="META").first()
            assert closed_long is not None
            assert closed_long.entry_snapshot_id == snap_a_id
            assert closed_long.qty == 10.0
            assert closed_long.side == "buy"

            # Flipped short position must have Snapshot B
            pos_short = session.query(PaperPosition).filter_by(symbol="META").first()
            assert pos_short is not None
            assert pos_short.qty == -15.0
            assert pos_short.entry_snapshot_id != snap_a_id
            snap_b = session.query(PaperEntrySnapshot).filter_by(snapshot_id=pos_short.entry_snapshot_id).first()
            assert snap_b is not None
            assert math.isclose(snap_b.conviction, 0.92, abs_tol=1e-6)

    def test_averaging_in_preserves_original_entry_snapshot(self, paper_store: PaperAccountStore):
        """Averaging into an existing position must NOT overwrite the initial entry snapshot id."""
        # Initial buy
        assert paper_store.apply_fill(
            client_order_id="ord_avg_1",
            symbol="AMZN",
            side="buy",
            qty=10.0,
            fill_price=180.0,
            provenance="signal_driven",
            conviction=0.80,
        )
        with session_scope(paper_store.Session) as session:
            pos = session.query(PaperPosition).filter_by(symbol="AMZN").first()
            orig_snap_id = pos.entry_snapshot_id
            assert orig_snap_id is not None

        # Subsequent buy (averaging in)
        assert paper_store.apply_fill(
            client_order_id="ord_avg_2",
            symbol="AMZN",
            side="buy",
            qty=10.0,
            fill_price=175.0,
            provenance="signal_driven",
            conviction=0.95,
        )
        with session_scope(paper_store.Session) as session:
            pos_avg = session.query(PaperPosition).filter_by(symbol="AMZN").first()
            assert pos_avg.qty == 20.0
            assert pos_avg.entry_snapshot_id == orig_snap_id, "Averaging in must preserve initial entry_snapshot_id"


# =============================================================================
# TASK 2: Stress Test Multi-Trade Excursion Calculation (Zero Collisions)
# =============================================================================

class TestMultiTradeExcursionStressAndCollisionIsolation:
    """Stress test multi-trade excursion calculation to prove isolated in-memory stores prevent 100% of collisions."""

    def test_overlapping_and_identical_timestamp_multi_trade_excursions(
        self,
        composer: RetrospectiveComposer,
        paper_store: PaperAccountStore,
        transactions_store: TransactionsStore,
        evaluation_engine: EvaluationEngine,
    ):
        """Verify that 5 overlapping, identical timestamp, and multi-duration trades for the same ticker
        receive strictly isolated and mathematically authentic excursions without portfolio collisions.
        """
        # Create 5 trades for AAPL:
        # Trade 1: Long, Jan 01 - Jan 10. Entry: 100.0, Exit: 120.0
        # Trade 2: Long, Jan 05 - Jan 15. Entry: 110.0, Exit: 130.0 (Overlaps Jan 05-10 with Trade 1)
        # Trade 3: Short, Jan 05 - Jan 15. Entry: 110.0, Exit: 95.0 (Identical timestamps to Trade 2, but SHORT)
        # Trade 4: Long, Jan 07 - Jan 08. Entry: 112.0, Exit: 118.0 (1-day duration nested inside Trades 1, 2, 3)
        # Trade 5: Long, Jan 01 - Jan 31. Entry: 100.0, Exit: 140.0 (Entire month span)

        trade_specs = [
            {"id": 101, "side": "BUY", "entry_dt": datetime(2026, 1, 1, tzinfo=timezone.utc), "exit_dt": datetime(2026, 1, 10, tzinfo=timezone.utc), "entry_p": 100.0, "exit_p": 120.0, "qty": 10.0},
            {"id": 102, "side": "BUY", "entry_dt": datetime(2026, 1, 5, tzinfo=timezone.utc), "exit_dt": datetime(2026, 1, 15, tzinfo=timezone.utc), "entry_p": 110.0, "exit_p": 130.0, "qty": 10.0},
            {"id": 103, "side": "SELL", "entry_dt": datetime(2026, 1, 5, tzinfo=timezone.utc), "exit_dt": datetime(2026, 1, 15, tzinfo=timezone.utc), "entry_p": 110.0, "exit_p": 95.0, "qty": 10.0},
            {"id": 104, "side": "BUY", "entry_dt": datetime(2026, 1, 7, tzinfo=timezone.utc), "exit_dt": datetime(2026, 1, 8, tzinfo=timezone.utc), "entry_p": 112.0, "exit_p": 118.0, "qty": 10.0},
            {"id": 105, "side": "BUY", "entry_dt": datetime(2026, 1, 1, tzinfo=timezone.utc), "exit_dt": datetime(2026, 1, 31, tzinfo=timezone.utc), "entry_p": 100.0, "exit_p": 140.0, "qty": 10.0},
        ]

        # Populate paper_store with closed trades
        with session_scope(paper_store.Session) as session:
            for spec in trade_specs:
                session.add(PaperClosedTrade(
                    trade_id=spec["id"],
                    strategy_id="test_collision_strat",
                    symbol="AAPL",
                    side=spec["side"],
                    qty=spec["qty"],
                    entry_ts=spec["entry_dt"],
                    entry_price=spec["entry_p"],
                    exit_ts=spec["exit_dt"],
                    exit_price=spec["exit_p"],
                    close_reason="target",
                    realized_pnl=(spec["exit_p"] - spec["entry_p"]) * spec["qty"] if spec["side"] == "BUY" else (spec["entry_p"] - spec["exit_p"]) * spec["qty"],
                    bridge_status="bridged",
                ))

        # Build full month historical pricing for AAPL
        dates = pd.date_range("2026-01-01", "2026-01-31", freq="D")
        highs = []
        lows = []
        closes = []

        for d in dates:
            day = d.day
            if day <= 4:
                # Jan 1 - 4: Moderate rise
                highs.append(105.0)
                lows.append(95.0)
                closes.append(102.0)
            elif day == 5 or day == 6:
                # Jan 5 - 6: Trade 2 & 3 open days
                highs.append(115.0)
                lows.append(108.0)
                closes.append(112.0)
            elif day == 7 or day == 8:
                # Jan 7 - 8: Trade 4 window!
                highs.append(120.0)
                lows.append(111.0)
                closes.append(118.0)
            elif day <= 10:
                # Jan 9 - 10: Trade 1 closes on Jan 10
                highs.append(125.0)
                lows.append(118.0)
                closes.append(120.0)
            elif day <= 15:
                # Jan 11 - 15: Big plunge & recovery before Trades 2 & 3 close!
                # High reaches 135.0, Low drops to 85.0!
                highs.append(135.0)
                lows.append(85.0)
                closes.append(110.0)
            else:
                # Jan 16 - 31: Late month surge
                highs.append(160.0)
                lows.append(80.0)
                closes.append(140.0)

        history_df = pd.DataFrame({"High": highs, "Low": lows, "Close": closes}, index=dates)
        data_provider = {"AAPL": history_df}

        # Mathematical Oracle calculations:
        # Trade 1 (Long, Jan 1-10, Entry 100):
        # Window: Jan 1-10. Max High = 125.0, Min Low = 95.0.
        # expected MFE = (125 - 100) / 100 = 0.25
        # expected MAE = (100 - 95) / 100 = 0.05
        # expected Edge = 0.25 / 0.05 = 5.0
        exp_t1_mfe, exp_t1_mae, exp_t1_edge = 0.25, 0.05, 5.0

        # Trade 2 (Long, Jan 5-15, Entry 110):
        # Window: Jan 5-15. Max High = 135.0, Min Low = 85.0.
        # expected MFE = (135 - 110) / 110 = 25 / 110 = 0.22727...
        # expected MAE = (110 - 85) / 110 = 25 / 110 = 0.22727...
        # expected Edge = 1.0
        exp_t2_mfe = 25.0 / 110.0
        exp_t2_mae = 25.0 / 110.0
        exp_t2_edge = 1.0

        # Trade 3 (Short, Jan 5-15, Entry 110):
        # Window: Jan 5-15.
        # Short favorable: min price = 85.0 -> MFE = (110 - 85) / 110 = 0.22727...
        # Short adverse: max price = 135.0 -> MAE = (135 - 110) / 110 = 0.22727...
        # expected Edge = 1.0
        exp_t3_mfe = 25.0 / 110.0
        exp_t3_mae = 25.0 / 110.0
        exp_t3_edge = 1.0

        # Trade 4 (Long, Jan 7-8, Entry 112):
        # Window: Jan 7-8. Max High = 120.0, Min Low = 111.0.
        # calculate_excursion_metrics rounds mae and mfe to 4 decimals:
        # mfe = round((120 - 112) / 112, 4) = round(0.07142857..., 4) = 0.0714
        # mae = round((112 - 111) / 112, 4) = round(0.00892857..., 4) = 0.0089
        # edge = mfe / mae = 0.0714 / 0.0089 = 8.0224719...
        exp_t4_mfe = round(8.0 / 112.0, 4)
        exp_t4_mae = round(1.0 / 112.0, 4)
        exp_t4_edge = exp_t4_mfe / exp_t4_mae

        # Trade 5 (Long, Jan 1-31, Entry 100):
        # Window: Jan 1-31. Max High = 160.0, Min Low = 80.0.
        # expected MFE = (160 - 100) / 100 = 0.60
        # expected MAE = (100 - 80) / 100 = 0.20
        # expected Edge = 0.60 / 0.20 = 3.0
        exp_t5_mfe, exp_t5_mae, exp_t5_edge = 0.60, 0.20, 3.0

        # 1. Evaluate in Forward Order
        forward_results = {}
        for spec in trade_specs:
            rec = composer.compose_trade_retrospective(spec["id"], data_provider=data_provider)
            assert rec is not None
            assert rec["excursion"]["evaluation_status"] == "available"
            forward_results[spec["id"]] = rec["excursion"]

        # Assert Trade 1 isolated metrics
        assert math.isclose(forward_results[101]["mfe"], exp_t1_mfe, abs_tol=1e-3)
        assert math.isclose(forward_results[101]["mae"], exp_t1_mae, abs_tol=1e-3)
        assert math.isclose(forward_results[101]["edge_ratio"], exp_t1_edge, abs_tol=1e-2)

        # Assert Trade 2 isolated metrics
        assert math.isclose(forward_results[102]["mfe"], exp_t2_mfe, abs_tol=1e-3)
        assert math.isclose(forward_results[102]["mae"], exp_t2_mae, abs_tol=1e-3)
        assert math.isclose(forward_results[102]["edge_ratio"], exp_t2_edge, abs_tol=1e-2)

        # Assert Trade 3 short isolated metrics
        assert math.isclose(forward_results[103]["mfe"], exp_t3_mfe, abs_tol=1e-3)
        assert math.isclose(forward_results[103]["mae"], exp_t3_mae, abs_tol=1e-3)

        # Assert Trade 4 (1-day nested trade) isolated metrics
        assert math.isclose(forward_results[104]["mfe"], exp_t4_mfe, abs_tol=1e-3)
        assert math.isclose(forward_results[104]["mae"], exp_t4_mae, abs_tol=1e-3)
        assert math.isclose(forward_results[104]["edge_ratio"], exp_t4_edge, abs_tol=1e-2)

        # Assert Trade 5 full month metrics
        assert math.isclose(forward_results[105]["mfe"], exp_t5_mfe, abs_tol=1e-3)
        assert math.isclose(forward_results[105]["mae"], exp_t5_mae, abs_tol=1e-3)
        assert math.isclose(forward_results[105]["edge_ratio"], exp_t5_edge, abs_tol=1e-2)

        # 2. Evaluate in Reverse Order (Trade 105 down to 101)
        reverse_ids = [105, 104, 103, 102, 101]
        for tid in reverse_ids:
            rec_rev = composer.compose_trade_retrospective(tid, data_provider=data_provider)
            assert rec_rev is not None
            # Byte-for-byte exact equality between forward and reverse evaluation!
            assert math.isclose(rec_rev["excursion"]["mfe"], forward_results[tid]["mfe"], abs_tol=1e-9)
            assert math.isclose(rec_rev["excursion"]["mae"], forward_results[tid]["mae"], abs_tol=1e-9)
            assert math.isclose(rec_rev["excursion"]["edge_ratio"], forward_results[tid]["edge_ratio"], abs_tol=1e-9)

        # 3. Randomized order evaluation across 10 iterations
        random.seed(42)
        all_ids = [s["id"] for s in trade_specs]
        for _ in range(10):
            shuffled = list(all_ids)
            random.shuffle(shuffled)
            for tid in shuffled:
                rec_rand = composer.compose_trade_retrospective(tid, data_provider=data_provider)
                assert math.isclose(rec_rand["excursion"]["mfe"], forward_results[tid]["mfe"], abs_tol=1e-9)
                assert math.isclose(rec_rand["excursion"]["mae"], forward_results[tid]["mae"], abs_tol=1e-9)
                assert math.isclose(rec_rand["excursion"]["edge_ratio"], forward_results[tid]["edge_ratio"], abs_tol=1e-9)


# =============================================================================
# TASK 3: Extreme Cohort Isolation Under 1,000 Pathological Manual Trades
# =============================================================================

class TestExtremeCohortIsolationStress1000Trades:
    """Challenge cohort isolation under extreme stress: inject 1,000 extreme manual trades
    and assert automated metrics remain unchanged with 10^-9 precision.
    """

    def test_1000_pathological_manual_trades_zero_tolerance_invariance(self):
        """Inject 1,000 pathological manual trades alongside 20 automated trades.
        Assert that automated metrics remain completely invariant to 10^-9 tolerance.
        """
        # 1. Baseline automated cohort: 20 realistic signal-driven trades
        automated_trades: list[dict[str, Any]] = []
        random.seed(12345)

        for i in range(20):
            # 12 winning trades, 8 losing trades -> win rate = 0.60
            is_win = (i < 12)
            strat = "TrendFollower" if i % 2 == 0 else "MeanReversion"
            pnl = round(150.0 + i * 25.0, 2) if is_win else round(-75.0 - i * 15.0, 2)
            conviction = round(0.65 + (i % 5) * 0.05, 2)
            holding_days = round(1.5 + (i % 4) * 0.5, 2)
            mae = round(0.02 + (i % 3) * 0.01, 4)
            mfe = round(0.08 + (i % 4) * 0.02, 4)
            edge = round(mfe / mae, 4) if mae > 0 else 2.0

            trade = {
                "trade_id": f"auto_{i+1}",
                "symbol": f"SYM_{i % 5}",
                "strategy_id": strat,
                "provenance": "signal_driven",
                "realized_pnl": pnl,
                "holding_period_days": holding_days,
                "bridge_status": "bridged",
                "entry_snapshot": {
                    "captured": True,
                    "conviction": conviction,
                    "macro_regime": "EXPANSION",
                    "strategy_id": strat,
                },
                "excursion": {
                    "evaluation_status": "available",
                    "mae": mae,
                    "mfe": mfe,
                    "edge_ratio": edge,
                },
            }
            automated_trades.append(trade)

        # Compute baseline metrics for automated cohort alone
        baseline_insights = generate_batch_retrospective_insights(composed_records=automated_trades)
        base_auto = baseline_insights["automated_cohort"]

        assert base_auto["total_trades"] == 20
        assert base_auto["winning_trades"] == 12
        assert base_auto["losing_trades"] == 8
        assert math.isclose(base_auto["win_rate"], 0.60, abs_tol=1e-6)
        assert base_auto["total_realized_pnl"] > 0
        assert base_auto["profit_factor"] is not None
        assert base_auto["calibration_brier_score"] is not None

        # 2. Construct 1,000 extreme, degenerate, and pathological manual trades
        pathological_manual_trades: list[dict[str, Any]] = []

        for m_idx in range(1000):
            t_cat = m_idx % 10
            man_trade: dict[str, Any] = {
                "trade_id": f"pathological_man_{m_idx+1}",
                "symbol": f"MAN_{m_idx % 20}",
                "provenance": "manual",
                "bridge_status": "disabled" if m_idx % 2 == 0 else "failed",
            }

            if t_cat == 0:
                # Extreme massive positive PnL (+$1,000,000,000.00)
                man_trade["realized_pnl"] = 1_000_000_000.00
                man_trade["holding_period_days"] = 0.5
            elif t_cat == 1:
                # Extreme massive negative PnL (-$1,000,000,000.00)
                man_trade["realized_pnl"] = -1_000_000_000.00
                man_trade["holding_period_days"] = 100.0
            elif t_cat == 2:
                # Zero holding period & zero PnL
                man_trade["realized_pnl"] = 0.0
                man_trade["holding_period_days"] = 0.0
            elif t_cat == 3:
                # 1,000-year holding period
                man_trade["realized_pnl"] = -500.0
                man_trade["holding_period_days"] = 365_000.0
            elif t_cat == 4:
                # Float NaN values across all numeric fields
                man_trade["realized_pnl"] = float("nan")
                man_trade["holding_period_days"] = float("nan")
                man_trade["excursion"] = {"mae": float("nan"), "mfe": float("nan"), "edge_ratio": float("nan")}
            elif t_cat == 5:
                # Float Inf / -Inf values
                man_trade["realized_pnl"] = float("inf") if m_idx % 2 == 0 else float("-inf")
                man_trade["holding_period_days"] = float("inf")
                man_trade["excursion"] = {"mae": float("inf"), "mfe": float("-inf"), "edge_ratio": float("nan")}
            elif t_cat == 6:
                # Corrupt string values in numeric positions
                man_trade["realized_pnl"] = "NOT_A_NUMBER"
                man_trade["holding_period_days"] = "corrupt_holding_str"
                man_trade["excursion"] = {"mae": "corrupt_mae", "mfe": "corrupt_mfe", "edge_ratio": "corrupt_edge"}
            elif t_cat == 7:
                # Empty / minimal dictionary with only provenance
                pass  # only contains trade_id, symbol, provenance
            elif t_cat == 8:
                # Hostile injection attempt: manual trade tries to claim conviction & automated strategy
                man_trade["realized_pnl"] = -99999.0
                man_trade["strategy_id"] = "TrendFollower"  # Same strategy name as automated!
                man_trade["conviction"] = 0.99  # Fake conviction!
                man_trade["entry_snapshot"] = {"conviction": 0.99, "strategy_id": "TrendFollower"}
            else:
                # Random wildly fluctuating values
                man_trade["realized_pnl"] = (random.random() - 0.5) * 10_000_000.0
                man_trade["holding_period_days"] = random.random() * 50.0

            pathological_manual_trades.append(man_trade)

        assert len(pathological_manual_trades) == 1000

        # 3. Combine and shuffle thoroughly
        combined_trades = list(automated_trades) + list(pathological_manual_trades)
        random.seed(999)
        random.shuffle(combined_trades)

        assert len(combined_trades) == 1020

        # 4. Generate batch insights under extreme stress
        stressed_insights = generate_batch_retrospective_insights(composed_records=combined_trades)
        stressed_auto = stressed_insights["automated_cohort"]
        stressed_manual = stressed_insights["manual_cohort"]

        # Verify manual cohort swallowed all 1,000 manual trades
        assert stressed_manual["total_trades"] == 1000

        # 5. ZERO-TOLERANCE MATHEMATICAL INVARIANCE CHECK (10^-9 precision)
        assert stressed_auto["total_trades"] == base_auto["total_trades"]
        assert stressed_auto["winning_trades"] == base_auto["winning_trades"]
        assert stressed_auto["losing_trades"] == base_auto["losing_trades"]
        assert stressed_auto["breakeven_trades"] == base_auto["breakeven_trades"]

        assert math.isclose(stressed_auto["win_rate"], base_auto["win_rate"], abs_tol=1e-9), (
            f"Automated win_rate drifted: {stressed_auto['win_rate']} vs {base_auto['win_rate']}"
        )
        assert math.isclose(stressed_auto["total_realized_pnl"], base_auto["total_realized_pnl"], abs_tol=1e-9), (
            f"Automated total_realized_pnl drifted: {stressed_auto['total_realized_pnl']} vs {base_auto['total_realized_pnl']}"
        )
        assert math.isclose(stressed_auto["profit_factor"], base_auto["profit_factor"], abs_tol=1e-9), (
            f"Automated profit_factor drifted: {stressed_auto['profit_factor']} vs {base_auto['profit_factor']}"
        )
        assert math.isclose(stressed_auto["mean_holding_period_days"], base_auto["mean_holding_period_days"], abs_tol=1e-9), (
            f"Automated holding_period drifted: {stressed_auto['mean_holding_period_days']} vs {base_auto['mean_holding_period_days']}"
        )
        assert math.isclose(stressed_auto["mean_mae"], base_auto["mean_mae"], abs_tol=1e-9), (
            f"Automated mean_mae drifted: {stressed_auto['mean_mae']} vs {base_auto['mean_mae']}"
        )
        assert math.isclose(stressed_auto["mean_mfe"], base_auto["mean_mfe"], abs_tol=1e-9), (
            f"Automated mean_mfe drifted: {stressed_auto['mean_mfe']} vs {base_auto['mean_mfe']}"
        )
        assert math.isclose(stressed_auto["mean_edge_ratio"], base_auto["mean_edge_ratio"], abs_tol=1e-9), (
            f"Automated mean_edge_ratio drifted: {stressed_auto['mean_edge_ratio']} vs {base_auto['mean_edge_ratio']}"
        )
        assert math.isclose(stressed_auto["calibration_brier_score"], base_auto["calibration_brier_score"], abs_tol=1e-9), (
            f"Automated Brier score drifted: {stressed_auto['calibration_brier_score']} vs {base_auto['calibration_brier_score']}"
        )

        # 6. Verify Per-Strategy Breakdowns within Automated Cohort
        # (Manual trades with fake strategy_id='TrendFollower' MUST NOT enter automated strategy breakdown!)
        assert set(stressed_auto["strategies"].keys()) == set(base_auto["strategies"].keys())
        for s_key in base_auto["strategies"]:
            b_strat = base_auto["strategies"][s_key]
            s_strat = stressed_auto["strategies"][s_key]
            assert s_strat["total_trades"] == b_strat["total_trades"]
            assert s_strat["winning_trades"] == b_strat["winning_trades"]
            assert s_strat["losing_trades"] == b_strat["losing_trades"]
            assert math.isclose(s_strat["win_rate"], b_strat["win_rate"], abs_tol=1e-9)
            assert math.isclose(s_strat["total_realized_pnl"], b_strat["total_realized_pnl"], abs_tol=1e-9)

        # 7. Assert Zero Forbidden Aggregate Keys at the Root Level
        root_keys = set(stressed_insights.keys())
        forbidden_present = root_keys.intersection(FORBIDDEN_ROOT_KEYS)
        assert not forbidden_present, f"Forbidden top-level aggregate performance keys leaked: {forbidden_present}"

        # 8. Assert Valid Expected Root Structure
        expected_root_keys = {
            "automated_cohort",
            "manual_cohort",
            "unrecorded_cohort",
            "contrastive_insights",
            "bridge_health",
        }
        assert expected_root_keys.issubset(root_keys)

    def test_three_way_cohort_stress_with_mixed_casings_and_unrecorded(self):
        """Stress test with 50 automated, 1,000 extreme manual, and 500 unrecorded historical trades.
        Verify that mixed provenance casings ('Signal_Driven', 'sIgNaL_dRiVeN', 'MANUAL')
        are resolved accurately, unrecorded trades never contaminate model cohorts, and
        contrastive insights generate clean narratives without None or NaN leakage.
        """
        import re
        forbidden_regex = re.compile(r"\b(None|NaN|nan|null)\b")

        # 50 automated trades with varied casings
        auto_trades = []
        casings = ["signal_driven", "Signal_Driven", "SIGNAL_DRIVEN", "sIgNaL_dRiVeN"]
        for i in range(50):
            prov = casings[i % len(casings)]
            auto_trades.append({
                "trade_id": f"auto_cas_{i+1}",
                "symbol": f"AUTO_{i%5}",
                "provenance": prov,
                "strategy_id": "AlphaFlow",
                "realized_pnl": 100.0 if i % 2 == 0 else -50.0,
                "holding_period_days": 2.0,
                "bridge_status": "bridged",
                "entry_snapshot": {"captured": True, "conviction": 0.80, "strategy_id": "AlphaFlow"},
                "excursion": {"evaluation_status": "available", "mae": 0.02, "mfe": 0.06, "edge_ratio": 3.0},
            })

        # Baseline automated metrics
        base_auto = _compute_cohort_metrics(auto_trades, is_automated=True)
        assert base_auto["total_trades"] == 50
        assert math.isclose(base_auto["win_rate"], 0.50, abs_tol=1e-6)

        # 1,000 pathological manual trades
        manual_casings = ["manual", "Manual", "MANUAL", "mAnUaL"]
        manual_trades = []
        for i in range(1000):
            prov = manual_casings[i % len(manual_casings)]
            manual_trades.append({
                "trade_id": f"man_{i+1}",
                "symbol": f"MAN_{i%10}",
                "provenance": prov,
                "realized_pnl": (i - 500) * 1000.0,
                "holding_period_days": float(i % 30),
                "bridge_status": "disabled",
            })

        # 500 unrecorded / pre-feature historical trades
        unrec_trades = []
        unrec_provs = ["unknown", "unrecorded", None, "", "legacy"]
        for i in range(500):
            prov = unrec_provs[i % len(unrec_provs)]
            unrec_trades.append({
                "trade_id": f"hist_{i+1}",
                "symbol": f"HIST_{i%10}",
                "provenance": prov,
                "realized_pnl": 50.0,
                "holding_period_days": 5.0,
                "bridge_status": "disabled",
            })

        all_records = auto_trades + manual_trades + unrec_trades
        random.seed(4242)
        random.shuffle(all_records)

        insights = generate_batch_retrospective_insights(composed_records=all_records)

        # Check cohort counts
        assert insights["automated_cohort"]["total_trades"] == 50
        assert insights["manual_cohort"]["total_trades"] == 1000
        assert insights["unrecorded_cohort"]["total_trades"] == 500

        # Check zero-tolerance invariance on automated cohort
        assert math.isclose(insights["automated_cohort"]["win_rate"], base_auto["win_rate"], abs_tol=1e-9)
        assert math.isclose(insights["automated_cohort"]["total_realized_pnl"], base_auto["total_realized_pnl"], abs_tol=1e-9)

        # Check unrecorded cohort note
        assert "excluded from systematic model evaluation" in insights["unrecorded_cohort"]["note"]

        # Check contrastive insights: zero forbidden tokens
        for insight_str in insights["contrastive_insights"]:
            match = forbidden_regex.search(insight_str)
            assert match is None, f"Forbidden token '{match.group(0)}' leaked in contrastive insight: {insight_str}"


class TestDegenerateExcursionBoundaryConditions:
    """Stress test excursion calculation against degenerate market & trade inputs."""

    def test_degenerate_zero_dollar_entry_price(
        self, composer: RetrospectiveComposer, paper_store: PaperAccountStore
    ):
        """A trade with $0.00 entry price must handle zero division cleanly without crashing."""
        with session_scope(paper_store.Session) as session:
            session.add(PaperClosedTrade(
                trade_id=9991,
                symbol="ZERO_P",
                side="BUY",
                qty=100.0,
                entry_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
                entry_price=0.0,  # Degenerate $0.00!
                exit_ts=datetime(2026, 1, 5, tzinfo=timezone.utc),
                exit_price=10.0,
                close_reason="close",
                realized_pnl=1000.0,
                bridge_status="bridged",
            ))

        dates = pd.date_range("2026-01-01", "2026-01-05", freq="D")
        history = pd.DataFrame({"High": [10.0]*5, "Low": [0.0]*5, "Close": [5.0]*5}, index=dates)

        rec = composer.compose_trade_retrospective(9991, data_provider={"ZERO_P": history})
        assert rec is not None
        # Must gracefully degrade to unavailable or null without unhandled ZeroDivisionError
        assert rec["excursion"]["evaluation_status"] in ("available", "evaluation data unavailable")
        assert "None" not in rec["narrative"]

    def test_instantaneous_zero_second_holding_duration(
        self, composer: RetrospectiveComposer, paper_store: PaperAccountStore
    ):
        """A trade closed instantaneously at the exact same microsecond as entry."""
        # Case A: same_ts aligns with the bar timestamp
        same_ts = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
        with session_scope(paper_store.Session) as session:
            session.add(PaperClosedTrade(
                trade_id=9992,
                symbol="INSTANT",
                side="BUY",
                qty=10.0,
                entry_ts=same_ts,
                entry_price=100.0,
                exit_ts=same_ts,  # 0 duration!
                exit_price=100.0,
                close_reason="scratch",
                realized_pnl=0.0,
                bridge_status="bridged",
            ))

        dates = pd.date_range("2026-02-01", "2026-02-01", freq="D")
        history = pd.DataFrame({"High": [100.0], "Low": [100.0], "Close": [100.0]}, index=dates)

        rec = composer.compose_trade_retrospective(9992, data_provider={"INSTANT": history})
        assert rec is not None
        assert rec["excursion"]["evaluation_status"] == "available"
        assert rec["excursion"]["mae"] == 0.0
        assert rec["excursion"]["mfe"] == 0.0
        assert "None" not in rec["narrative"]

        # Case B: intraday time where no bars match the hold period slice
        intraday_ts = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)
        with session_scope(paper_store.Session) as session:
            session.add(PaperClosedTrade(
                trade_id=99921,
                symbol="INSTANT_INTRADAY",
                side="BUY",
                qty=10.0,
                entry_ts=intraday_ts,
                entry_price=100.0,
                exit_ts=intraday_ts,
                exit_price=100.0,
                close_reason="scratch",
                realized_pnl=0.0,
                bridge_status="bridged",
            ))
        rec_intra = composer.compose_trade_retrospective(99921, data_provider={"INSTANT_INTRADAY": history})
        assert rec_intra is not None
        assert rec_intra["excursion"]["evaluation_status"] == "evaluation data unavailable"
        assert "Hold-period pricing data missing or insufficient" in rec_intra["excursion"]["reason"]

    def test_missing_ticker_in_historical_data_provider(
        self, composer: RetrospectiveComposer, paper_store: PaperAccountStore
    ):
        """When data_provider completely lacks the requested ticker, return unavailable excursion cleanly."""
        with session_scope(paper_store.Session) as session:
            session.add(PaperClosedTrade(
                trade_id=9993,
                symbol="UNKNOWN_TICKER",
                side="BUY",
                qty=10.0,
                entry_ts=datetime(2026, 3, 1, tzinfo=timezone.utc),
                entry_price=50.0,
                exit_ts=datetime(2026, 3, 5, tzinfo=timezone.utc),
                exit_price=55.0,
                close_reason="target",
                realized_pnl=50.0,
                bridge_status="bridged",
            ))

        # Data provider has other symbols, but not UNKNOWN_TICKER
        data_provider = {"AAPL": pd.DataFrame({"High": [150.0], "Low": [140.0], "Close": [145.0]})}
        rec = composer.compose_trade_retrospective(9993, data_provider=data_provider)
        assert rec is not None
        assert rec["excursion"]["evaluation_status"] == "evaluation data unavailable"
        assert rec["excursion"]["mae"] is None
        assert rec["excursion"]["mfe"] is None
        assert "Hold-period excursion metrics unavailable" in rec["narrative"]


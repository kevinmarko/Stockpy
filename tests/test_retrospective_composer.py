"""tests/test_retrospective_composer.py — Comprehensive Unit Test Suite for RetrospectiveComposer

Authoritative Specifications:
- .agents/ORIGINAL_REQUEST.md (§ R4)
- .agents/PROJECT.md (§ 2 Retrospective Composer & § Feature Inventory)
- .agents/worker_m2/DISPATCH.md
- .agents/explorer_survey_2/retrospective_learning_loop_survey_report.md (§ 4 & § 7)

Verification Coverage:
1. RetrospectiveComposer initialization, dependency injection, and DB isolation.
2. Full happy path: bridged signal-driven trade with snapshot (excursion available, calibration binned).
3. WP-C: Historical trade with entry_snapshot_id=None strictly returns decision_context_status: "not_captured",
   provenance: "unknown", refusing to infer from strategy_id.
4. WP-E: Byte-for-byte mathematical fidelity test comparing composer's MAE/MFE/Edge Ratio directly against
   EvaluationEngine.evaluate_portfolio() for both long and short positions.
5. Strict bridge gate: Trade with bridge_status="disabled" or "failed" returns
   evaluation_status: "evaluation data unavailable" and null excursion metrics.
6. Missing OHLC bars: Bridged trade with no hold-period bars returns evaluation_status: "evaluation data unavailable".
7. Manual trade: returns provenance: "manual", calibration: {"status": "not_applicable"}.
8. Uncalibrated trade: snapshot with conviction=None returns calibration: {"status": "not_applicable"}.
9. Calibration curve binning: maps conviction into reliability diagram and returns empirical win rate when sample >= 5.
10. Nonexistent trade_id returns None; empty store returns [].
11. Batch composition with symbol and strategy_id filtering.
12. Boundary conditions: degenerate entry price ($0.00), instantaneous 0-day close, extreme price levels.
13. Narrative formatting: zero None/NaN leakage across all composed records.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from data.paper_account_store import (
    PaperAccountStore,
    PaperClosedTrade,
    PaperEntrySnapshot,
    session_scope,
)
from evaluation_engine import EvaluationEngine
from pilots.retrospective_composer import (
    RetrospectiveComposer,
    build_trade_narrative,
    compose_retrospectives_batch,
    compose_trade_retrospective,
)
from transactions_store import TransactionsStore

# =============================================================================
# Fixtures & Isolated Test Setup
# =============================================================================

@pytest.fixture
def isolated_db_url(tmp_path) -> str:
    """Explicit per-test file-backed SQLite database URL."""
    db_file = tmp_path / "composer_unit_test.db"
    return f"sqlite:///{db_file}"


@pytest.fixture
def paper_store(isolated_db_url: str) -> PaperAccountStore:
    """Isolated PaperAccountStore instance."""
    return PaperAccountStore(db_url=isolated_db_url)


@pytest.fixture
def transactions_store_inst(isolated_db_url: str) -> TransactionsStore:
    """Isolated TransactionsStore instance."""
    return TransactionsStore(db_url=isolated_db_url)


@pytest.fixture
def evaluation_engine_inst() -> EvaluationEngine:
    """EvaluationEngine instance."""
    return EvaluationEngine()


@pytest.fixture
def composer(
    paper_store: PaperAccountStore,
    transactions_store_inst: TransactionsStore,
    evaluation_engine_inst: EvaluationEngine,
    isolated_db_url: str,
) -> RetrospectiveComposer:
    """Pre-wired RetrospectiveComposer bound explicitly to isolated test databases."""
    return RetrospectiveComposer(
        paper_store=paper_store,
        transactions_store=transactions_store_inst,
        evaluation_engine=evaluation_engine_inst,
        db_url=isolated_db_url,
    )


# =============================================================================
# 1. Initialization & Dependency Injection
# =============================================================================

class TestRetrospectiveComposerInitialization:
    """Verify clean initialization, dependency injection, and read-only behavior."""

    def test_default_initialization_creates_readonly_engines(self, isolated_db_url):
        """Default constructor instantiates dependent stores and engines safely."""
        c = RetrospectiveComposer(db_url=isolated_db_url)
        assert c.paper_store is not None
        assert c.transactions_store is not None
        assert c.evaluation_engine is not None
        assert c.db_url == isolated_db_url

    def test_explicit_dependency_injection(self, paper_store, transactions_store_inst, evaluation_engine_inst):
        """Injected stores and engines are preserved without replacement."""
        c = RetrospectiveComposer(
            paper_store=paper_store,
            transactions_store=transactions_store_inst,
            evaluation_engine=evaluation_engine_inst,
        )
        assert c.paper_store is paper_store
        assert c.transactions_store is transactions_store_inst
        assert c.evaluation_engine is evaluation_engine_inst


# =============================================================================
# 2. Full Happy Path (Bridged + Snapshot + Excursion + Calibration)
# =============================================================================

class TestRetrospectiveComposerHappyPath:
    """Verify complete synthesis for a fully captured, bridged, signal-driven trade."""

    def test_full_happy_path_bridged_signal_driven_trade(self, composer, paper_store, transactions_store_inst):
        """Comprehensive verification of all clauses and sections for a bridged signal-driven trade."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        entry_ts = now - timedelta(days=5)
        exit_ts = now

        # 1. Seed snapshot in paper_entry_snapshots
        snap_id = "snap_happy_1"
        with session_scope(paper_store.Session) as session:
            snap = PaperEntrySnapshot(
                snapshot_id=snap_id,
                symbol="NVDA",
                strategy_id="trend_following",
                pilot_id="pilot_alpha_1",
                experiment_arm="challenger",
                entry_ts=entry_ts,
                entry_price=100.0,
                side="buy",
                qty=10.0,
                provenance="signal_driven",
                provenance_tag="model_generated",
                conviction=0.85,
                macro_regime="Expansion",
                signal_score=0.92,
                raw_forecast=120.0,
                decision_rationale="Breakout confirmed on high volume",
            )
            session.add(snap)

        # 2. Seed bridged trade in TransactionsStore (for excursion and calibration)
        bridged_id = transactions_store_inst.record_trade(
            symbol="NVDA",
            side="long",
            entry_ts=entry_ts,
            entry_price=100.0,
            shares=10.0,
            strategy="trend_following",
            conviction=0.85,
        )
        transactions_store_inst.close_trade(bridged_id, exit_ts, 115.0)

        # 3. Seed closed paper trade in paper_closed_trades
        with session_scope(paper_store.Session) as session:
            pct = PaperClosedTrade(
                trade_id=101,
                strategy_id="trend_following",
                pilot_id="pilot_alpha_1",
                experiment_arm="challenger",
                symbol="NVDA",
                side="BUY",
                qty=10.0,
                entry_ts=entry_ts,
                entry_price=100.0,
                exit_ts=exit_ts,
                exit_price=115.0,
                commission=0.0,
                realized_pnl=150.0,
                realized_pnl_pct=0.15,
                holding_period_days=5.0,
                close_reason="target_reached",
                entry_snapshot_id=snap_id,
                bridge_status="bridged",
                bridged_trade_id=bridged_id,
                bridge_error=None,
                bridged_at=now,
            )
            session.add(pct)

        # 4. Prepare mock price history
        dates = pd.date_range(start=entry_ts - timedelta(days=1), periods=7, freq="D")
        mock_history = pd.DataFrame({
            "High": [100.0, 105.0, 112.0, 118.0, 116.0, 120.0, 119.0],
            "Low": [98.0, 96.0, 102.0, 110.0, 111.0, 114.0, 115.0],
            "Close": [99.0, 104.0, 110.0, 115.0, 114.0, 118.0, 115.0],
        }, index=dates)
        mock_history.index = mock_history.index.tz_localize(None)
        data_provider = {"NVDA": mock_history}

        # 5. Compose retrospective
        rec = composer.compose_trade_retrospective(101, data_provider=data_provider)
        assert rec is not None

        # Core trade metadata
        assert rec["trade_id"] == 101
        assert rec["symbol"] == "NVDA"
        assert rec["side"] == "BUY"
        assert rec["qty"] == 10.0
        assert math.isclose(rec["entry_price"], 100.0)
        assert math.isclose(rec["exit_price"], 115.0)
        assert math.isclose(rec["realized_pnl"], 150.0)
        assert math.isclose(rec["realized_pnl_pct"], 0.15)
        assert math.isclose(rec["holding_period_days"], 5.0)
        assert rec["close_reason"] == "target_reached"
        assert rec["provenance"] == "signal_driven"

        # Snapshot verification
        snap_out = rec["snapshot"]
        assert snap_out["decision_context_status"] == "captured"
        assert snap_out["captured"] is True
        assert snap_out["provenance"] == "signal_driven"
        assert snap_out["conviction"] == 0.85
        assert snap_out["macro_regime"] == "Expansion"
        assert snap_out["reason"] is None

        # Excursion verification (Hold period low=96 -> MAE=0.04; high=120 -> MFE=0.20)
        exc = rec["excursion"]
        assert exc["evaluation_status"] == "available"
        assert exc["bridge_reached"] is True
        assert exc["mae"] is not None and math.isclose(exc["mae"], 0.04, abs_tol=1e-3)
        assert exc["mfe"] is not None and math.isclose(exc["mfe"], 0.20, abs_tol=1e-3)
        assert exc["edge_ratio"] is not None and math.isclose(exc["edge_ratio"], 5.0, abs_tol=1e-2)

        # Calibration verification
        cal = rec["calibration"]
        assert cal["status"] in ("insufficient_sample", "insufficient_data")
        assert cal["conviction"] == 0.85
        assert cal["bin_range"] == [0.8, 0.9]
        assert cal["bin_center"] == 0.85

        # Narrative verification
        narrative = rec["narrative"]
        assert "Signal-driven" in narrative
        assert "trend_following" in narrative
        assert "Expansion" in narrative
        assert "MFE +20.0%" in narrative
        assert "MAE -4.0%" in narrative
        assert "None" not in narrative
        assert "NaN" not in narrative


# =============================================================================
# 3. Strict Anti-Fabrication Gates
# =============================================================================

class TestStrictAntiFabricationGates:
    """Verify refusal to fabricate data when snapshots or bridge connections are missing."""

    def test_wp_c_historical_trade_without_snapshot_never_inferred(self, composer, paper_store):
        """WP-C: Pre-feature historical trade with entry_snapshot_id=None reports 'not captured',
        provenance='unknown', and NEVER infers from strategy_id.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with session_scope(paper_store.Session) as session:
            pct = PaperClosedTrade(
                trade_id=201,
                strategy_id="trend_following",  # High-conviction strategy name present
                symbol="MSFT",
                side="BUY",
                qty=20.0,
                entry_ts=now - timedelta(days=3),
                entry_price=300.0,
                exit_ts=now,
                exit_price=320.0,
                commission=0.0,
                realized_pnl=400.0,
                realized_pnl_pct=0.0667,
                holding_period_days=3.0,
                close_reason="close",
                entry_snapshot_id=None,  # Pre-feature trade lacks snapshot
                bridge_status="bridged",
            )
            session.add(pct)

        rec = composer.compose_trade_retrospective(201)
        assert rec is not None

        # STRICT GATE: Provenance must NOT be upgraded to signal_driven despite strategy_id!
        assert rec["provenance"] == "unknown"
        # Canonical wire vocabulary is the underscore form (matching
        # `provenance`/`bridge_status`'s own convention and
        # webapp/src/api/types.ts's declared literal union) -- a prior
        # version of this test asserted BOTH the space and underscore
        # spellings two lines apart, reconciled only by a custom
        # cross-format __eq__ shim in production code (see
        # pilots/retrospective_composer.py's STATUS_* constants).
        assert rec["snapshot"]["decision_context_status"] == "not_captured"
        assert rec["snapshot"]["captured"] is False
        assert rec["snapshot"]["provenance"] == "unknown"
        assert rec["snapshot"]["reason"] == "not captured"
        assert rec["snapshot"]["conviction"] is None

        # Narrative should reflect unrecorded provenance
        assert "unrecorded provenance" in rec["narrative"]
        assert "None" not in rec["narrative"]

    def test_bridge_failed_gate_returns_null_excursion(self, composer, paper_store):
        """If bridge_status == 'failed', excursion MUST report 'evaluation data unavailable'
        and null metrics (mae: None, mfe: None, edge_ratio: None).
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with session_scope(paper_store.Session) as session:
            pct = PaperClosedTrade(
                trade_id=202,
                strategy_id="mean_reversion",
                symbol="AAPL",
                side="BUY",
                qty=15.0,
                entry_ts=now - timedelta(days=2),
                entry_price=150.0,
                exit_ts=now,
                exit_price=155.0,
                commission=0.0,
                realized_pnl=75.0,
                realized_pnl_pct=0.0333,
                holding_period_days=2.0,
                close_reason="close",
                bridge_status="failed",
                bridge_error="OperationalError: database is locked",
            )
            session.add(pct)

        rec = composer.compose_trade_retrospective(202)
        assert rec is not None

        exc = rec["excursion"]
        assert exc["evaluation_status"] == "evaluation data unavailable"
        assert exc["bridge_reached"] is False
        assert exc["mae"] is None
        assert exc["mfe"] is None
        assert exc["edge_ratio"] is None
        assert exc["realized_slippage"] is None
        assert "did not reach TransactionsStore bridge" in exc["reason"]
        assert "Hold-period excursion metrics unavailable" in rec["narrative"]

    def test_bridge_disabled_gate_returns_null_excursion(self, composer, paper_store):
        """If bridge_status == 'disabled', excursion metrics must be strictly null."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with session_scope(paper_store.Session) as session:
            pct = PaperClosedTrade(
                trade_id=203,
                strategy_id="breakout",
                symbol="GOOGL",
                side="BUY",
                qty=5.0,
                entry_ts=now - timedelta(days=1),
                entry_price=175.0,
                exit_ts=now,
                exit_price=180.0,
                commission=0.0,
                realized_pnl=25.0,
                realized_pnl_pct=0.0286,
                holding_period_days=1.0,
                close_reason="close",
                bridge_status="disabled",
            )
            session.add(pct)

        rec = composer.compose_trade_retrospective(203)
        assert rec is not None
        assert rec["excursion"]["evaluation_status"] == "evaluation data unavailable"
        assert rec["excursion"]["bridge_reached"] is False
        assert rec["excursion"]["mae"] is None
        assert rec["excursion"]["mfe"] is None

    def test_missing_ohlc_bars_for_bridged_trade(self, composer, paper_store):
        """If trade is bridged but hold-period OHLC bars are missing or empty,
        excursion reports 'evaluation data unavailable' with bridge_reached=True and null metrics.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with session_scope(paper_store.Session) as session:
            pct = PaperClosedTrade(
                trade_id=204,
                symbol="AMZN",
                side="BUY",
                qty=10.0,
                entry_ts=now - timedelta(days=2),
                entry_price=180.0,
                exit_ts=now,
                exit_price=185.0,
                commission=0.0,
                realized_pnl=50.0,
                realized_pnl_pct=0.0278,
                holding_period_days=2.0,
                close_reason="close",
                bridge_status="bridged",
            )
            session.add(pct)

        # Empty historical data provider
        data_provider = {"AMZN": pd.DataFrame(columns=["High", "Low", "Close"])}
        rec = composer.compose_trade_retrospective(204, data_provider=data_provider)
        assert rec is not None

        exc = rec["excursion"]
        assert exc["evaluation_status"] == "evaluation data unavailable"
        assert exc["bridge_reached"] is True
        assert exc["mae"] is None
        assert exc["mfe"] is None
        assert exc["edge_ratio"] is None
        assert "pricing data missing" in exc["reason"].lower() or "unavailable" in exc["reason"].lower()


# =============================================================================
# 4. WP-E Mathematical Fidelity (evaluate_portfolio() Byte-for-Byte Diff)
# =============================================================================

class TestWPEMathematicalFidelity:
    """Verify byte-for-byte mathematical alignment between composer and EvaluationEngine.evaluate_portfolio()."""

    def test_wp_e_long_position_fidelity(self, composer, transactions_store_inst, evaluation_engine_inst, isolated_db_url):
        """WP-E: Compare composer MAE, MFE, and Edge Ratio directly against evaluate_portfolio() output."""
        entry_ts = datetime(2026, 6, 1, 9, 30, 0, tzinfo=timezone.utc)
        exit_ts = datetime(2026, 6, 5, 16, 0, 0, tzinfo=timezone.utc)
        t_id = transactions_store_inst.record_trade(
            symbol="SPY",
            side="long",
            entry_ts=entry_ts,
            entry_price=500.0,
            shares=20.0,
        )
        transactions_store_inst.close_trade(t_id, exit_ts, 520.0)

        # Create multi-day price excursion
        date_range = pd.date_range(start="2026-06-01", end="2026-06-05", freq="D")
        history_df = pd.DataFrame({
            "High": [500.0, 510.0, 530.0, 525.0, 522.0],  # Max high = 530.0 -> MFE = 30 / 500 = 0.06
            "Low": [495.0, 485.0, 502.0, 515.0, 518.0],   # Min low = 485.0 -> MAE = 15 / 500 = 0.03
            "Close": [498.0, 505.0, 528.0, 520.0, 520.0],
        }, index=date_range)
        history_df.index = history_df.index.tz_localize(None)
        data_provider = {"SPY": history_df}

        # Direct evaluation engine call. This patches `resolve_database_url`
        # (the default-construction fallback path -- `db_url = db_url or
        # resolve_database_url()`, transactions_store.py) rather than
        # `TransactionsStore.__init__` itself. That distinction is what keeps
        # this a genuinely independent reference computation: an explicit
        # `db_url` (as the composer's own internal isolated `:memory:` store
        # always passes) is never overridden by `resolve_database_url`, so
        # this patch redirects ONLY `evaluate_portfolio()`'s own default,
        # no-store-injected construction below -- it can no longer also
        # silently redirect the composer's supposedly-isolated single-trade
        # store onto this same shared file-backed DB, which a prior version
        # of this test did (via a class-level `TransactionsStore.__init__`
        # monkeypatch), defeating the isolation this test exists to prove and
        # making it blind to a corrupted composer reconstruction (side flip,
        # price doubling, hold-window relocation all passed unnoticed).
        test_df = pd.DataFrame([{"Symbol": "SPY", "Price": 500.0, "position_size": 10000.0}])
        with patch("transactions_store.resolve_database_url", return_value=isolated_db_url):
            eval_df = evaluation_engine_inst.evaluate_portfolio(test_df, data_provider=data_provider)
        expected_mae = float(eval_df.iloc[0]["MAE"])
        expected_mfe = float(eval_df.iloc[0]["MFE"])
        expected_edge = float(eval_df.iloc[0]["Edge Ratio"])

        # Composer call -- its own internal single-trade store is genuinely
        # isolated (an explicit `:memory:` db_url), unaffected by the patch
        # above.
        rec = composer.compose_trade_retrospective(t_id, data_provider=data_provider)
        assert rec is not None

        # BYTE-FOR-BYTE FIDELITY ASSERTIONS
        assert math.isclose(rec["excursion"]["mae"], expected_mae, abs_tol=1e-9)
        assert math.isclose(rec["excursion"]["mfe"], expected_mfe, abs_tol=1e-9)
        assert math.isclose(rec["excursion"]["edge_ratio"], expected_edge, abs_tol=1e-9)

    def test_wp_e_short_position_fidelity(self, composer, transactions_store_inst, evaluation_engine_inst, isolated_db_url):
        """WP-E: Verify byte-for-byte fidelity for a short position."""
        entry_ts = datetime(2026, 6, 10, 9, 30, 0, tzinfo=timezone.utc)
        exit_ts = datetime(2026, 6, 14, 16, 0, 0, tzinfo=timezone.utc)
        t_id = transactions_store_inst.record_trade(
            symbol="TSLA",
            side="short",
            entry_ts=entry_ts,
            entry_price=200.0,
            shares=10.0,
        )
        transactions_store_inst.close_trade(t_id, exit_ts, 180.0)

        date_range = pd.date_range(start="2026-06-10", end="2026-06-14", freq="D")
        history_df = pd.DataFrame({
            "High": [200.0, 210.0, 205.0, 195.0, 185.0],  # For short: adverse high = 210 -> MAE = 10 / 200 = 0.05
            "Low": [198.0, 195.0, 182.0, 178.0, 180.0],   # For short: favorable low = 178 -> MFE = 22 / 200 = 0.11
            "Close": [199.0, 202.0, 185.0, 180.0, 180.0],
        }, index=date_range)
        history_df.index = history_df.index.tz_localize(None)
        data_provider = {"TSLA": history_df}

        # See test_wp_e_long_position_fidelity's comment: patching
        # `resolve_database_url` (not `TransactionsStore.__init__`) keeps the
        # composer's own internal isolated `:memory:` store genuinely
        # isolated, since it always passes an explicit `db_url`.
        test_df = pd.DataFrame([{"Symbol": "TSLA", "Price": 200.0, "position_size": 2000.0}])
        with patch("transactions_store.resolve_database_url", return_value=isolated_db_url):
            eval_df = evaluation_engine_inst.evaluate_portfolio(test_df, data_provider=data_provider)
        expected_mae = float(eval_df.iloc[0]["MAE"])
        expected_mfe = float(eval_df.iloc[0]["MFE"])
        expected_edge = float(eval_df.iloc[0]["Edge Ratio"])

        rec = composer.compose_trade_retrospective(t_id, data_provider=data_provider)
        assert rec is not None

        assert math.isclose(rec["excursion"]["mae"], expected_mae, abs_tol=1e-9)
        assert math.isclose(rec["excursion"]["mfe"], expected_mfe, abs_tol=1e-9)
        assert math.isclose(rec["excursion"]["edge_ratio"], expected_edge, abs_tol=1e-9)


# =============================================================================
# 4b. Multi-Trade Excursion Collision Regression
# =============================================================================

class TestMultiTradeExcursionCollisionRegression:
    """Verify that multiple closed trades for the same symbol with different hold periods
    and price paths receive strictly distinct and accurate MAE/MFE/Edge Ratio per trade,
    preventing the collision bug where earlier trades received the latest trade's metrics.
    """

    def test_multi_trade_excursion_distinct_metrics_same_symbol(
        self, composer, paper_store, transactions_store_inst, evaluation_engine_inst
    ):
        """Two sequential long trades for AAPL with different hold periods and price paths
        must each receive their own true MAE, MFE, and Edge Ratio, not colliding.
        """
        tA_id = transactions_store_inst.record_trade(
            "AAPL", "long", datetime(2026, 1, 1, tzinfo=timezone.utc), 100.0, 10.0
        )
        transactions_store_inst.close_trade(tA_id, datetime(2026, 1, 5, tzinfo=timezone.utc), 110.0)

        tB_id = transactions_store_inst.record_trade(
            "AAPL", "long", datetime(2026, 1, 10, tzinfo=timezone.utc), 100.0, 10.0
        )
        transactions_store_inst.close_trade(tB_id, datetime(2026, 1, 15, tzinfo=timezone.utc), 105.0)

        with session_scope(paper_store.Session) as session:
            session.add(PaperClosedTrade(
                trade_id=1,
                symbol="AAPL",
                side="BUY",
                qty=10.0,
                entry_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
                entry_price=100.0,
                exit_ts=datetime(2026, 1, 5, tzinfo=timezone.utc),
                exit_price=110.0,
                close_reason="target",
                realized_pnl=100.0,
                bridge_status="bridged",
                bridged_trade_id=tA_id,
            ))
            session.add(PaperClosedTrade(
                trade_id=2,
                symbol="AAPL",
                side="BUY",
                qty=10.0,
                entry_ts=datetime(2026, 1, 10, tzinfo=timezone.utc),
                entry_price=100.0,
                exit_ts=datetime(2026, 1, 15, tzinfo=timezone.utc),
                exit_price=105.0,
                close_reason="target",
                realized_pnl=50.0,
                bridge_status="bridged",
                bridged_trade_id=tB_id,
            ))

        dates = pd.date_range("2026-01-01", "2026-01-20", freq="D")
        highs = [
            100.0, 120.0, 150.0, 130.0, 110.0,  # Trade A days (Jan 1-5): max=150.0
            100.0, 100.0, 100.0, 100.0,         # Inter-trade days (Jan 6-9)
            100.0, 106.0, 104.0, 102.0, 101.0, 105.0,  # Trade B days (Jan 10-15): max=106.0
            100.0, 100.0, 100.0, 100.0, 100.0,  # Post-trade days
        ]
        lows = [
            98.0, 96.0, 95.0, 97.0, 99.0,       # Trade A days: min=95.0
            99.0, 99.0, 99.0, 99.0,             # Inter-trade days
            98.0, 95.0, 92.0, 90.0, 94.0, 97.0, # Trade B days: min=90.0
            99.0, 99.0, 99.0, 99.0, 99.0,
        ]
        closes = [100.0] * 20
        history = pd.DataFrame({"High": highs, "Low": lows, "Close": closes}, index=dates)

        recA = composer.compose_trade_retrospective(1, data_provider={"AAPL": history})
        recB = composer.compose_trade_retrospective(2, data_provider={"AAPL": history})

        assert recA is not None
        assert recB is not None

        excA = recA["excursion"]
        excB = recB["excursion"]

        # Assert no collision: metrics are distinct
        assert excA["mfe"] != excB["mfe"]
        assert excA["mae"] != excB["mae"]
        assert excA["edge_ratio"] != excB["edge_ratio"]

        # Assert exact calculations per trade
        # Trade A: Entry 100, max High 150 -> MFE = 0.50; min Low 95 -> MAE = 0.05; Edge = 0.50/0.05 = 10.0
        assert math.isclose(excA["mfe"], 0.50, abs_tol=1e-3)
        assert math.isclose(excA["mae"], 0.05, abs_tol=1e-3)
        assert math.isclose(excA["edge_ratio"], 10.0, abs_tol=1e-2)

        # Trade B: Entry 100, max High 106 -> MFE = 0.06; min Low 90 -> MAE = 0.10; Edge = 0.06/0.10 = 0.60
        assert math.isclose(excB["mfe"], 0.06, abs_tol=1e-3)
        assert math.isclose(excB["mae"], 0.10, abs_tol=1e-3)
        assert math.isclose(excB["edge_ratio"], 0.60, abs_tol=1e-2)

    def test_multi_trade_with_opposite_sides_same_symbol(
        self, composer, paper_store, transactions_store_inst, evaluation_engine_inst
    ):
        """A long trade and a short trade for MSFT must receive distinct direction-appropriate metrics."""
        t_long = transactions_store_inst.record_trade(
            "MSFT", "long", datetime(2026, 2, 1, tzinfo=timezone.utc), 200.0, 5.0
        )
        transactions_store_inst.close_trade(t_long, datetime(2026, 2, 5, tzinfo=timezone.utc), 210.0)

        t_short = transactions_store_inst.record_trade(
            "MSFT", "short", datetime(2026, 2, 10, tzinfo=timezone.utc), 200.0, 5.0
        )
        transactions_store_inst.close_trade(t_short, datetime(2026, 2, 15, tzinfo=timezone.utc), 190.0)

        with session_scope(paper_store.Session) as session:
            session.add(PaperClosedTrade(
                trade_id=10,
                symbol="MSFT",
                side="BUY",
                qty=5.0,
                entry_ts=datetime(2026, 2, 1, tzinfo=timezone.utc),
                entry_price=200.0,
                exit_ts=datetime(2026, 2, 5, tzinfo=timezone.utc),
                exit_price=210.0,
                close_reason="target",
                realized_pnl=50.0,
                bridge_status="bridged",
                bridged_trade_id=t_long,
            ))
            session.add(PaperClosedTrade(
                trade_id=11,
                symbol="MSFT",
                side="SELL",
                qty=5.0,
                entry_ts=datetime(2026, 2, 10, tzinfo=timezone.utc),
                entry_price=200.0,
                exit_ts=datetime(2026, 2, 15, tzinfo=timezone.utc),
                exit_price=190.0,
                close_reason="target",
                realized_pnl=50.0,
                bridge_status="bridged",
                bridged_trade_id=t_short,
            ))

        dates = pd.date_range("2026-02-01", "2026-02-20", freq="D")
        highs = [
            200.0, 215.0, 220.0, 210.0, 210.0,  # Long: high 220 -> MFE = 20/200 = 0.10
            200.0, 200.0, 200.0, 200.0,
            200.0, 208.0, 204.0, 202.0, 195.0, 190.0,  # Short: high 208 -> MAE = 8/200 = 0.04
            200.0, 200.0, 200.0, 200.0, 200.0,
        ]
        lows = [
            196.0, 198.0, 199.0, 205.0, 208.0,  # Long: low 196 -> MAE = 4/200 = 0.02
            200.0, 200.0, 200.0, 200.0,
            198.0, 190.0, 184.0, 180.0, 185.0, 190.0,  # Short: low 180 -> MFE = 20/200 = 0.10
            200.0, 200.0, 200.0, 200.0, 200.0,
        ]
        closes = [200.0] * 20
        history = pd.DataFrame({"High": highs, "Low": lows, "Close": closes}, index=dates)

        rec_long = composer.compose_trade_retrospective(10, data_provider={"MSFT": history})
        rec_short = composer.compose_trade_retrospective(11, data_provider={"MSFT": history})

        assert rec_long is not None
        assert rec_short is not None

        # Long: MFE = (220-200)/200 = 0.10, MAE = (200-196)/200 = 0.02, Edge = 0.10/0.02 = 5.0
        assert math.isclose(rec_long["excursion"]["mfe"], 0.10, abs_tol=1e-3)
        assert math.isclose(rec_long["excursion"]["mae"], 0.02, abs_tol=1e-3)
        assert math.isclose(rec_long["excursion"]["edge_ratio"], 5.0, abs_tol=1e-2)

        # Short: MFE = (200-180)/200 = 0.10, MAE = (208-200)/200 = 0.04, Edge = 0.10/0.04 = 2.5
        assert math.isclose(rec_short["excursion"]["mfe"], 0.10, abs_tol=1e-3)
        assert math.isclose(rec_short["excursion"]["mae"], 0.04, abs_tol=1e-3)
        assert math.isclose(rec_short["excursion"]["edge_ratio"], 2.5, abs_tol=1e-2)


# =============================================================================
# 5. Calibration Curve Integration
# =============================================================================

class TestCalibrationIntegration:
    """Verify conviction mapping to reliability diagram and uncalibrated fallbacks."""

    def test_manual_trade_calibration_not_applicable(self, composer, paper_store):
        """Manual discretionary trade must report calibration status 'not_applicable'."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        snap_id = "snap_manual_1"
        with session_scope(paper_store.Session) as session:
            session.add(PaperEntrySnapshot(
                snapshot_id=snap_id,
                symbol="META",
                strategy_id="manual",
                entry_ts=now - timedelta(days=1),
                entry_price=500.0,
                side="buy",
                qty=5.0,
                provenance="manual",
                decision_rationale="Discretionary earnings play",
            ))
            session.add(PaperClosedTrade(
                trade_id=301,
                symbol="META",
                side="BUY",
                qty=5.0,
                entry_ts=now - timedelta(days=1),
                entry_price=500.0,
                exit_ts=now,
                exit_price=510.0,
                commission=0.0,
                realized_pnl=50.0,
                realized_pnl_pct=0.02,
                holding_period_days=1.0,
                close_reason="close",
                entry_snapshot_id=snap_id,
                bridge_status="bridged",
            ))

        dates = pd.date_range(start=now - timedelta(days=2), periods=4, freq="D")
        mock_meta = pd.DataFrame({
            "High": [500.0, 515.0, 520.0, 518.0],
            "Low": [490.0, 495.0, 505.0, 508.0],
            "Close": [495.0, 510.0, 512.0, 510.0],
        }, index=dates)
        mock_meta.index = mock_meta.index.tz_localize(None)

        rec = composer.compose_trade_retrospective(301, data_provider={"META": mock_meta})
        assert rec is not None

        assert rec["provenance"] == "manual"
        # Canonical underscore form -- see the decision_context_status note
        # above for why this file no longer asserts both spellings.
        assert rec["calibration"]["status"] == "not_applicable"
        assert rec["calibration"]["conviction"] is None
        assert "not applicable" in rec["calibration"]["reason"].lower()
        assert "model calibration not applicable for manual trades" in rec["narrative"]

    def test_signal_trade_without_conviction_calibration_not_applicable(self, composer, paper_store):
        """Signal-driven trade where model conviction was None reports calibration not_applicable."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        snap_id = "snap_no_conv_1"
        with session_scope(paper_store.Session) as session:
            session.add(PaperEntrySnapshot(
                snapshot_id=snap_id,
                symbol="NFLX",
                strategy_id="momentum",
                entry_ts=now - timedelta(days=1),
                entry_price=600.0,
                side="buy",
                qty=2.0,
                provenance="signal_driven",
                conviction=None,  # Model produced no conviction score
            ))
            session.add(PaperClosedTrade(
                trade_id=302,
                symbol="NFLX",
                side="BUY",
                qty=2.0,
                entry_ts=now - timedelta(days=1),
                entry_price=600.0,
                exit_ts=now,
                exit_price=610.0,
                commission=0.0,
                realized_pnl=20.0,
                realized_pnl_pct=0.0167,
                holding_period_days=1.0,
                close_reason="close",
                entry_snapshot_id=snap_id,
                bridge_status="bridged",
            ))

        rec = composer.compose_trade_retrospective(302)
        assert rec is not None
        assert rec["calibration"]["status"] == "not_applicable"
        assert rec["calibration"]["conviction"] is None

    def test_calibration_curve_binned_with_empirical_trades(self, composer, paper_store, transactions_store_inst):
        """When 5+ closed trades exist in a conviction bin, report empirical bin_win_rate."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Seed 5 closed trades in TransactionsStore with conviction=0.85 (4 wins, 1 loss -> win_rate=0.80)
        for i in range(5):
            is_win = (i < 4)
            exit_p = 110.0 if is_win else 90.0
            t_id = transactions_store_inst.record_trade(
                symbol="NVDA",
                side="long",
                entry_ts=now - timedelta(days=10 + i),
                entry_price=100.0,
                shares=10.0,
                conviction=0.85,
            )
            transactions_store_inst.close_trade(t_id, now - timedelta(days=5 + i), exit_p)

        # Seed trade under evaluation
        snap_id = "snap_cal_binned_1"
        with session_scope(paper_store.Session) as session:
            session.add(PaperEntrySnapshot(
                snapshot_id=snap_id,
                symbol="NVDA",
                strategy_id="trend",
                entry_ts=now - timedelta(days=2),
                entry_price=100.0,
                side="buy",
                qty=10.0,
                provenance="signal_driven",
                conviction=0.85,
            ))
            session.add(PaperClosedTrade(
                trade_id=303,
                symbol="NVDA",
                side="BUY",
                qty=10.0,
                entry_ts=now - timedelta(days=2),
                entry_price=100.0,
                exit_ts=now,
                exit_price=105.0,
                commission=0.0,
                realized_pnl=50.0,
                realized_pnl_pct=0.05,
                holding_period_days=2.0,
                close_reason="close",
                entry_snapshot_id=snap_id,
                bridge_status="bridged",
            ))

        rec = composer.compose_trade_retrospective(303)
        assert rec is not None

        cal = rec["calibration"]
        assert cal["status"] == "available"
        assert cal["conviction"] == 0.85
        assert cal["bin_trade_count"] == 5
        assert cal["bin_win_rate"] is not None
        assert math.isclose(cal["bin_win_rate"], 0.80, abs_tol=1e-4)
        assert math.isclose(cal["calibration_error"], abs(0.80 - 0.85), abs_tol=1e-4)

    def test_calibration_curve_insufficient_sample_status_when_under_five_trades(
        self, composer, paper_store, transactions_store_inst
    ):
        """When fewer than 5 closed trades exist in a conviction bin, report insufficient_sample."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Seed only 2 closed trades in TransactionsStore with conviction=0.85
        for i in range(2):
            t_id = transactions_store_inst.record_trade(
                symbol="NVDA",
                side="long",
                entry_ts=now - timedelta(days=10 + i),
                entry_price=100.0,
                shares=10.0,
                conviction=0.85,
            )
            transactions_store_inst.close_trade(t_id, now - timedelta(days=5 + i), 110.0)

        snap_id = "snap_cal_insufficient_1"
        with session_scope(paper_store.Session) as session:
            session.add(PaperEntrySnapshot(
                snapshot_id=snap_id,
                symbol="NVDA",
                strategy_id="trend",
                entry_ts=now - timedelta(days=2),
                entry_price=100.0,
                side="buy",
                qty=10.0,
                provenance="signal_driven",
                conviction=0.85,
            ))
            session.add(PaperClosedTrade(
                trade_id=304,
                symbol="NVDA",
                side="BUY",
                qty=10.0,
                entry_ts=now - timedelta(days=2),
                entry_price=100.0,
                exit_ts=now,
                exit_price=105.0,
                commission=0.0,
                realized_pnl=50.0,
                realized_pnl_pct=0.05,
                holding_period_days=2.0,
                close_reason="close",
                entry_snapshot_id=snap_id,
                bridge_status="bridged",
            ))

        rec = composer.compose_trade_retrospective(304)
        assert rec is not None

        cal = rec["calibration"]
        # Must report insufficient_sample (or insufficient_data via DualStatusStr)
        assert cal["status"] in ("insufficient_sample", "insufficient_data")
        assert cal["status"] == "insufficient_sample"
        assert cal["conviction"] == 0.85
        assert cal["bin_trade_count"] == 2
        assert cal["bin_win_rate"] is None
        assert "insufficient sample" in cal["reason"].lower()


# =============================================================================
# 6. Batch Composition & Query Filters
# =============================================================================

class TestBatchCompositionAndQueries:
    """Verify batch composition, limits, filtering, and empty-state contracts."""

    def test_nonexistent_trade_id_returns_none(self, composer):
        """Querying a nonexistent trade ID returns None without raising."""
        assert composer.compose_trade_retrospective(999999) is None
        assert composer.compose_trade_retrospective("nonexistent_uuid") is None

    def test_empty_store_batch_returns_empty_list(self, composer):
        """Querying batch on an empty store strictly returns []."""
        batch = composer.compose_retrospectives_batch(limit=100)
        assert batch == []

    def test_batch_filters_by_symbol_and_strategy(self, composer, paper_store):
        """Batch composition correctly applies symbol and strategy_id query filters."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with session_scope(paper_store.Session) as session:
            session.add(PaperClosedTrade(
                trade_id=401,
                symbol="AAPL",
                strategy_id="trend",
                side="BUY",
                qty=10.0,
                entry_ts=now - timedelta(days=1),
                entry_price=150.0,
                exit_ts=now,
                exit_price=155.0,
                commission=0.0,
                realized_pnl=50.0,
                realized_pnl_pct=0.0333,
                holding_period_days=1.0,
                close_reason="close",
            ))
            session.add(PaperClosedTrade(
                trade_id=402,
                symbol="MSFT",
                strategy_id="trend",
                side="BUY",
                qty=10.0,
                entry_ts=now - timedelta(days=1),
                entry_price=300.0,
                exit_ts=now,
                exit_price=310.0,
                commission=0.0,
                realized_pnl=100.0,
                realized_pnl_pct=0.0333,
                holding_period_days=1.0,
                close_reason="close",
            ))
            session.add(PaperClosedTrade(
                trade_id=403,
                symbol="AAPL",
                strategy_id="mean_reversion",
                side="BUY",
                qty=10.0,
                entry_ts=now - timedelta(days=1),
                entry_price=150.0,
                exit_ts=now,
                exit_price=148.0,
                commission=0.0,
                realized_pnl=-20.0,
                realized_pnl_pct=-0.0133,
                holding_period_days=1.0,
                close_reason="close",
            ))

        # Filter by symbol
        aapl_batch = composer.compose_retrospectives_batch(symbol="AAPL")
        assert len(aapl_batch) == 2
        assert all(t["symbol"] == "AAPL" for t in aapl_batch)

        # Filter by strategy
        trend_batch = composer.compose_retrospectives_batch(strategy_id="trend")
        assert len(trend_batch) == 2
        assert all(t["strategy_id"] == "trend" for t in trend_batch)

        # Filter by both
        combined_batch = composer.compose_retrospectives_batch(symbol="AAPL", strategy_id="trend")
        assert len(combined_batch) == 1
        assert combined_batch[0]["trade_id"] == 401


# =============================================================================
# 7. Boundary Conditions & Numerical Robustness
# =============================================================================

class TestBoundaryConditions:
    """Verify numerical safety across degenerate inputs and edge cases."""

    def test_instantaneous_zero_holding_period(self, composer, paper_store):
        """Trade closed at the exact same entry timestamp reports holding_period_days == 0.0."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with session_scope(paper_store.Session) as session:
            session.add(PaperClosedTrade(
                trade_id=501,
                symbol="QQQ",
                side="BUY",
                qty=50.0,
                entry_ts=now,
                entry_price=450.0,
                exit_ts=now,
                exit_price=450.0,
                commission=0.0,
                realized_pnl=0.0,
                realized_pnl_pct=0.0,
                holding_period_days=0.0,
                close_reason="cancelled_fill",
            ))

        rec = composer.compose_trade_retrospective(501)
        assert rec is not None
        assert rec["holding_period_days"] == 0.0
        assert "breakeven" in rec["narrative"]
        assert "0.0 days" in rec["narrative"]

    def test_degenerate_zero_entry_price_handles_safely(self, composer, paper_store):
        """Entry price of $0.00 does not raise ZeroDivisionError and sets realized_pnl_pct to None."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with session_scope(paper_store.Session) as session:
            session.add(PaperClosedTrade(
                trade_id=502,
                symbol="ZERO",
                side="BUY",
                qty=100.0,
                entry_ts=now - timedelta(days=1),
                entry_price=0.0,
                exit_ts=now,
                exit_price=5.0,
                commission=0.0,
                realized_pnl=500.0,
                realized_pnl_pct=None,  # Degenerate
                holding_period_days=1.0,
                close_reason="close",
            ))

        rec = composer.compose_trade_retrospective(502)
        assert rec is not None
        assert rec["realized_pnl_pct"] is None
        assert "percentage return unavailable due to degenerate entry price" in rec["narrative"]
        assert "None" not in rec["narrative"]
        assert "NaN" not in rec["narrative"]

    def test_narrative_zero_leakage_across_all_branches(self):
        """Verify build_trade_narrative refuses to emit 'None' or 'NaN' in any field."""
        forbidden_regex = re.compile(r"\b(None|NaN|nan|null)\b", re.IGNORECASE)

        test_cases = [
            # Missing everything
            {"provenance": "unknown", "side": "buy"},
            # Missing duration and percentage
            {"provenance": "manual", "side": "sell", "entry_price": 10.0, "exit_price": 8.0, "pnl": 2.0},
            # Signal driven missing conviction and regime
            {"provenance": "signal_driven", "side": "buy", "strategy_id": "trend", "entry_price": 50.0},
            # Bridge not reached
            {"provenance": "signal_driven", "side": "buy", "entry_price": 50.0, "bridge_reached": False},
            # Bars missing
            {"provenance": "manual", "side": "buy", "entry_price": 50.0, "bars_available": False},
        ]

        for tc in test_cases:
            text = build_trade_narrative(**tc)
            assert forbidden_regex.search(text) is None, f"Forbidden substring leaked in narrative: '{text}'"


# =============================================================================
# 8. Module-Level Convenience Functions
# =============================================================================

class TestModuleLevelFunctions:
    """Verify module-level compose_trade_retrospective and compose_retrospectives_batch."""

    def test_module_level_compose_trade_retrospective(self, composer, paper_store):
        """Module function passes through to composer method."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with session_scope(paper_store.Session) as session:
            session.add(PaperClosedTrade(
                trade_id=601,
                symbol="NVDA",
                side="BUY",
                qty=10.0,
                entry_ts=now - timedelta(days=1),
                entry_price=120.0,
                exit_ts=now,
                exit_price=125.0,
                commission=0.0,
                realized_pnl=50.0,
                realized_pnl_pct=0.0417,
                holding_period_days=1.0,
                close_reason="close",
            ))

        rec = compose_trade_retrospective(601, composer=composer)
        assert rec is not None
        assert rec["trade_id"] == 601
        assert rec["symbol"] == "NVDA"

    def test_module_level_compose_retrospectives_batch(self, composer, paper_store):
        """Module batch function passes through to composer method."""
        batch = compose_retrospectives_batch(limit=10, composer=composer)
        assert isinstance(batch, list)

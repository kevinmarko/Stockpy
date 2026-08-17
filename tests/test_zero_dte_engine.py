"""
Tests for pilots/zero_dte_engine.py (0DTE ORB & Volatility Squeeze Breakout Engine).
"""

import ast
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from unittest.mock import MagicMock, patch

from pilots.zero_dte_engine import (
    OpeningRange,
    SqueezeResult,
    ZeroDteContract,
    ZeroDteBreakoutSignal,
    ZeroDteExitSignal,
    compute_opening_range,
    detect_volatility_squeeze,
    scan_0dte_breakouts,
    evaluate_0dte_exits,
    parse_chain_data,
    execute_0dte_trade,
    execute_0dte_exits,
    manage_0dte_exits,
)


def _generate_intraday_bars(
    start_time: str = "2026-08-14 09:30:00",
    num_bars: int = 30,
    base_price: float = 100.0,
    trend: float = 0.0,
    volatility: float = 0.5,
    base_volume: float = 1000.0,
) -> pd.DataFrame:
    """Generates synthetic 1-minute intraday bars."""
    t0 = datetime.fromisoformat(start_time)
    bars = []
    curr = base_price
    for i in range(num_bars):
        ts = t0 + timedelta(minutes=i)
        drift = trend * i
        o = curr + drift
        h = o + abs(volatility) * 0.8
        l = o - abs(volatility) * 0.8
        c = o + (0.1 if trend >= 0 else -0.1)
        vol = base_volume + (i * 10)
        bars.append({
            "timestamp": ts,
            "open": o,
            "high": max(h, o, c),
            "low": min(l, o, c),
            "close": c,
            "volume": vol,
        })
        curr = c
    return pd.DataFrame(bars)


# ---------------------------------------------------------------------------
# 1. Opening Range Breakout (ORB) Tests
# ---------------------------------------------------------------------------

def test_compute_opening_range_standard_15min():
    """Verifies that 15-minute ORB isolates exactly the first 15 bars."""
    df = _generate_intraday_bars(num_bars=30, base_price=100.0, trend=0.1, volatility=1.0)
    orb = compute_opening_range(df, range_minutes=15)

    assert isinstance(orb, OpeningRange)
    assert orb.valid is True
    assert orb.range_minutes == 15
    assert orb.bars_count == 15
    assert orb.high == pytest.approx(df.iloc[:15]["high"].max(), abs=1e-4)
    assert orb.low == pytest.approx(df.iloc[:15]["low"].min(), abs=1e-4)
    assert orb.volume == pytest.approx(df.iloc[:15]["volume"].sum(), abs=1e-4)
    assert orb.range_width == pytest.approx(orb.high - orb.low, abs=1e-4)
    assert orb["high"] == orb.high
    assert orb.to_dict()["valid"] is True


def test_compute_opening_range_custom_window():
    """Verifies custom opening range window (e.g. 5 minutes or 30 minutes)."""
    df = _generate_intraday_bars(num_bars=20, base_price=200.0, trend=0.0, volatility=0.5)
    orb5 = compute_opening_range(df, range_minutes=5)
    assert orb5.bars_count == 5
    assert orb5.range_minutes == 5

    orb30 = compute_opening_range(df, range_minutes=30)
    # df only has 20 bars, so all 20 are included
    assert orb30.bars_count == 20


def test_compute_opening_range_empty_and_degenerate():
    """Verifies graceful handling of empty or degenerate inputs."""
    empty_orb = compute_opening_range([])
    assert empty_orb.valid is False
    assert empty_orb.bars_count == 0
    assert empty_orb.high == 0.0

    none_orb = compute_opening_range(None)
    assert none_orb.valid is False

    # List of dicts format
    dict_bars = [
        {"timestamp": "2026-08-14 09:30:00", "open": 100, "high": 105, "low": 99, "close": 102, "volume": 500},
        {"timestamp": "2026-08-14 09:31:00", "open": 102, "high": 106, "low": 101, "close": 104, "volume": 700},
    ]
    orb_dict = compute_opening_range(dict_bars, range_minutes=15)
    assert orb_dict.valid is True
    assert orb_dict.high == 106
    assert orb_dict.low == 99
    assert orb_dict.volume == 1200


# ---------------------------------------------------------------------------
# 2. TTM Volatility Squeeze Tests
# ---------------------------------------------------------------------------

def test_detect_volatility_squeeze_compression():
    """
    Verifies that low-volatility flat consolidation compresses Bollinger Bands
    inside Keltner Channels (Squeeze ON).
    """
    # Create 30 bars with very tight range (BB inside KC)
    bars = []
    for i in range(30):
        bars.append({
            "high": 100.10,
            "low": 99.90,
            "close": 100.00 + (0.01 if i % 2 == 0 else -0.01),
            "volume": 1000,
        })

    res = detect_volatility_squeeze(bars, bb_period=20, bb_std=2.0, kc_period=20, kc_mult=1.5)
    assert isinstance(res, SqueezeResult)
    assert res.squeeze_on is True
    assert res.status == "SQUEEZE_ON"
    assert res.compression_ratio < 1.0
    assert res.bb_upper <= res.kc_upper + 1e-4
    assert res.bb_lower >= res.kc_lower - 1e-4


def test_detect_volatility_squeeze_release_bullish():
    """
    Verifies Squeeze Release into Bullish Expansion:
    Tight consolidation for 20 bars, then rapid price expansion upwards.
    """
    bars = []
    # 20 bars of tight squeeze
    for i in range(20):
        bars.append({
            "high": 100.10,
            "low": 99.90,
            "close": 100.00,
            "volume": 1000,
        })
    # 3 breakout bars expanding upward
    bars.append({"high": 102.0, "low": 100.0, "close": 101.5, "volume": 5000})
    bars.append({"high": 104.0, "low": 101.5, "close": 103.5, "volume": 8000})
    bars.append({"high": 106.0, "low": 103.5, "close": 105.5, "volume": 10000})

    res = detect_volatility_squeeze(bars, bb_period=20, bb_std=2.0, kc_period=20, kc_mult=1.5)
    assert res.squeeze_fired is True
    assert res.direction == "BULLISH"
    assert res.momentum > 0
    assert res.status == "SQUEEZE_RELEASE_BULLISH"


def test_detect_volatility_squeeze_release_bearish():
    """
    Verifies Squeeze Release into Bearish Expansion:
    Tight consolidation for 20 bars, then rapid price expansion downwards.
    """
    bars = []
    for i in range(20):
        bars.append({
            "high": 100.10,
            "low": 99.90,
            "close": 100.00,
            "volume": 1000,
        })
    # 3 breakdown bars expanding downward
    bars.append({"high": 100.0, "low": 98.0, "close": 98.5, "volume": 5000})
    bars.append({"high": 98.5, "low": 96.0, "close": 96.5, "volume": 8000})
    bars.append({"high": 96.5, "low": 94.0, "close": 94.5, "volume": 10000})

    res = detect_volatility_squeeze(bars, bb_period=20, bb_std=2.0, kc_period=20, kc_mult=1.5)
    assert res.squeeze_fired is True
    assert res.direction == "BEARISH"
    assert res.momentum < 0
    assert res.status == "SQUEEZE_RELEASE_BEARISH"


def test_detect_volatility_squeeze_degenerate():
    """Verifies degenerate behavior on empty or single bar input."""
    res_empty = detect_volatility_squeeze([])
    assert res_empty.status == "NO_DATA"
    assert res_empty.squeeze_on is False

    res_one = detect_volatility_squeeze([{"high": 10, "low": 9, "close": 9.5, "volume": 100}])
    assert res_one.status == "NO_DATA"


# ---------------------------------------------------------------------------
# 3. 0DTE Breakout Scanner Tests
# ---------------------------------------------------------------------------

def test_scan_0dte_breakouts_bullish_call():
    """
    Verifies bullish breakout signal generation and ATM/1-OTM Call contract selection.
    """
    # 15 bars defining ORB [99.50, 101.50]
    bars = []
    t0 = datetime(2026, 8, 14, 9, 30)
    for i in range(15):
        bars.append({
            "timestamp": t0 + timedelta(minutes=i),
            "open": 100.0,
            "high": 101.50,
            "low": 99.50,
            "close": 100.50,
            "volume": 1000,
        })
    # Breakout bar 16: price shoots up to 103.0 with high volume
    bars.append({
        "timestamp": t0 + timedelta(minutes=15),
        "open": 101.50,
        "high": 103.50,
        "low": 101.20,
        "close": 103.00,
        "volume": 3000,  # 3x volume
    })

    current_quote = {"price": 103.00, "volume": 3000}

    # Option chain with 0DTE contracts
    chain = [
        {"symbol": "SPY", "contract_symbol": "SPY260814C00100000", "strike": 100.0, "option_type": "CALL", "dte": 0.0, "delta": 0.85, "bid": 3.0, "ask": 3.10},
        {"symbol": "SPY", "contract_symbol": "SPY260814C00103000", "strike": 103.0, "option_type": "CALL", "dte": 0.0, "delta": 0.50, "bid": 1.20, "ask": 1.25},
        {"symbol": "SPY", "contract_symbol": "SPY260814C00104000", "strike": 104.0, "option_type": "CALL", "dte": 0.0, "delta": 0.42, "bid": 0.65, "ask": 0.70},
        {"symbol": "SPY", "contract_symbol": "SPY260814P00103000", "strike": 103.0, "option_type": "PUT", "dte": 0.0, "delta": -0.50, "bid": 1.15, "ask": 1.20},
    ]

    signal = scan_0dte_breakouts(
        symbol="SPY",
        intraday_bars=bars,
        current_quote=current_quote,
        chain_data=chain,
        range_minutes=15,
        volume_threshold_mult=1.25,
    )

    assert isinstance(signal, ZeroDteBreakoutSignal)
    assert signal.signal_type == "BULLISH_BREAKOUT"
    assert signal.action == "BUY_CALL"
    assert signal.current_price == 103.00
    assert signal.orb_high == 101.50
    assert signal.confidence > 0.60
    assert signal.selected_contract is not None
    assert signal.selected_contract["option_type"] == "CALL"
    assert 0.40 <= signal.selected_contract["delta"] <= 0.55
    assert signal.selected_contract["strike"] in [103.0, 104.0]


def test_scan_0dte_breakouts_bearish_put():
    """
    Verifies bearish breakdown signal generation and ATM/1-OTM Put contract selection.
    """
    bars = []
    t0 = datetime(2026, 8, 14, 9, 30)
    for i in range(15):
        bars.append({
            "timestamp": t0 + timedelta(minutes=i),
            "open": 100.0,
            "high": 101.50,
            "low": 99.50,
            "close": 100.50,
            "volume": 1000,
        })
    # Breakdown bar 16: price falls to 98.0 with high volume
    bars.append({
        "timestamp": t0 + timedelta(minutes=15),
        "open": 99.50,
        "high": 99.80,
        "low": 97.80,
        "close": 98.00,
        "volume": 2500,
    })

    current_quote = {"price": 98.00, "volume": 2500}

    chain = [
        {"symbol": "QQQ", "contract_symbol": "QQQ260814P00098000", "strike": 98.0, "option_type": "PUT", "dte": 0.0, "delta": -0.48, "bid": 1.05, "ask": 1.10},
        {"symbol": "QQQ", "contract_symbol": "QQQ260814P00097000", "strike": 97.0, "option_type": "PUT", "dte": 0.0, "delta": -0.40, "bid": 0.55, "ask": 0.60},
        {"symbol": "QQQ", "contract_symbol": "QQQ260814C00098000", "strike": 98.0, "option_type": "CALL", "dte": 0.0, "delta": 0.52, "bid": 1.10, "ask": 1.15},
    ]

    signal = scan_0dte_breakouts(
        symbol="QQQ",
        intraday_bars=bars,
        current_quote=current_quote,
        chain_data=chain,
        range_minutes=15,
        volume_threshold_mult=1.25,
    )

    assert signal.signal_type == "BEARISH_BREAKDOWN"
    assert signal.action == "BUY_PUT"
    assert signal.current_price == 98.00
    assert signal.orb_low == 99.50
    assert signal.selected_contract is not None
    assert signal.selected_contract["option_type"] == "PUT"
    assert -0.55 <= signal.selected_contract["delta"] <= -0.40


def test_scan_0dte_breakouts_no_signal_inside_range():
    """Verifies that price inside opening range does not trigger breakout."""
    df = _generate_intraday_bars(num_bars=25, base_price=100.0, trend=0.0, volatility=0.2)
    current_quote = {"price": 100.05, "volume": 1000}

    signal = scan_0dte_breakouts(
        symbol="SPY",
        intraday_bars=df,
        current_quote=current_quote,
        chain_data=[],
    )

    assert signal.signal_type == "NO_SIGNAL"
    assert signal.action == "NO_ACTION"
    assert signal.selected_contract is None


# ---------------------------------------------------------------------------
# 4. Fast Risk & Lifecycle Exit Tests
# ---------------------------------------------------------------------------

def test_evaluate_0dte_exits_profit_target():
    """Verifies profit target exit when gain reaches +75%."""
    pos = {
        "position_id": "pos-001",
        "contract_symbol": "SPY 2026-08-14 $500.00 CALL",
        "symbol": "SPY",
        "entry_price": 2.00,
        "quantity": 2,
    }
    quotes = {
        "SPY 2026-08-14 $500.00 CALL": 3.60,  # +80% gain
    }

    exits = evaluate_0dte_exits(
        positions=[pos],
        current_time="2026-08-14T11:00:00",
        current_quotes=quotes,
        profit_target_pct=0.75,
        stop_loss_pct=0.30,
    )

    assert len(exits) == 1
    assert exits[0].exit_type == "EXIT_PROFIT_TARGET"
    assert exits[0].pnl_pct == pytest.approx(0.80, abs=1e-3)
    assert exits[0].urgent is False


def test_evaluate_0dte_exits_stop_loss():
    """Verifies stop loss exit when loss reaches -30%."""
    pos = {
        "position_id": "pos-002",
        "contract_symbol": "QQQ 2026-08-14 $450.00 PUT",
        "symbol": "QQQ",
        "entry_price": 2.00,
        "quantity": 1,
    }
    quotes = {
        "QQQ 2026-08-14 $450.00 PUT": 1.30,  # -35% loss
    }

    exits = evaluate_0dte_exits(
        positions=[pos],
        current_time="2026-08-14T11:30:00",
        current_quotes=quotes,
        profit_target_pct=0.75,
        stop_loss_pct=0.30,
    )

    assert len(exits) == 1
    assert exits[0].exit_type == "EXIT_STOP_LOSS"
    assert exits[0].pnl_pct == pytest.approx(-0.35, abs=1e-3)
    assert exits[0].urgent is True


def test_evaluate_0dte_exits_hard_time_stop():
    """Verifies mandatory hard time stop at or after 15:45 ET regardless of P&L."""
    pos = {
        "position_id": "pos-003",
        "contract_symbol": "SPY 2026-08-14 $500.00 CALL",
        "symbol": "SPY",
        "entry_price": 2.00,
        "quantity": 1,
    }
    quotes = {
        "SPY 2026-08-14 $500.00 CALL": 2.20,  # Small +10% gain
    }

    exits = evaluate_0dte_exits(
        positions=[pos],
        current_time=datetime(2026, 8, 14, 15, 48),  # 15:48 ET
        current_quotes=quotes,
        hard_exit_time="15:45",
    )

    assert len(exits) == 1
    assert exits[0].exit_type == "EXIT_HARD_TIME_STOP"
    assert exits[0].urgent is True


def test_execute_0dte_trade_single_leg():
    """Verifies that execute_0dte_trade submits a 0DTE single-leg option order with strategy_name='0DTE Momentum Breakout'."""
    from data.paper_account_store import PaperAccountStore
    store = PaperAccountStore(db_url="sqlite:///:memory:")

    res = execute_0dte_trade(
        symbol="SPY",
        side="buy",
        strike=500.0,
        expiration="2026-08-14",
        contracts=2,
        store=store,
        quote_price=2.50,
    )

    assert res["ok"] is True
    assert res["strategy_name"] == "0DTE Momentum Breakout"
    assert res["contracts"] == 2
    assert "SPY 2026-08-14 $500.00 CALL" in res.get("contract_symbol", res.get("symbol", ""))

    # Verify position is recorded
    positions = store.get_open_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "SPY 2026-08-14 $500.00 CALL"
    assert positions[0].qty == 2.0


def test_evaluate_0dte_exits_exit_reason_codes():
    """Verifies that evaluate_0dte_exits generates exact reason codes (HARD_TIME_STOP_1545, PROFIT_TARGET_75, STOP_LOSS_30)."""
    from data.paper_account_store import PaperPosition

    # 1. Hard time stop at 15:45
    pos1 = PaperPosition(symbol="SPY 2026-08-14 $500.00 CALL", qty=1.0, avg_entry_price=2.00)
    exits_time = evaluate_0dte_exits(
        positions=[pos1],
        current_time_str="15:45",
        current_quotes={"SPY 2026-08-14 $500.00 CALL": 2.10},
    )
    assert len(exits_time) == 1
    assert exits_time[0]["exit_reason"] == "HARD_TIME_STOP_1545"
    assert exits_time[0].exit_reason == "HARD_TIME_STOP_1545"

    # 2. Profit target at +80% P&L
    exits_tp = evaluate_0dte_exits(
        positions=[pos1],
        current_time_str="13:30",
        current_quotes={"SPY 2026-08-14 $500.00 CALL": 3.60},
    )
    assert len(exits_tp) == 1
    assert exits_tp[0]["exit_reason"] == "PROFIT_TARGET_75"
    assert exits_tp[0].exit_type == "EXIT_PROFIT_TARGET"

    # 3. Stop loss at -35% P&L
    exits_sl = evaluate_0dte_exits(
        positions=[pos1],
        current_time_str="13:30",
        current_quotes={"SPY 2026-08-14 $500.00 CALL": 1.30},
    )
    assert len(exits_sl) == 1
    assert exits_sl[0]["exit_reason"] == "STOP_LOSS_30"
    assert exits_sl[0].exit_type == "EXIT_STOP_LOSS"


def test_execute_0dte_exits_closing():
    """Verifies that execute_0dte_exits closes positions in PaperAccountStore."""
    from data.paper_account_store import PaperAccountStore
    store = PaperAccountStore(db_url="sqlite:///:memory:")

    # Open position
    execute_0dte_trade(
        symbol="SPY",
        side="call",
        strike=500.0,
        expiration="2026-08-14",
        contracts=1,
        store=store,
        quote_price=2.00,
    )
    assert len(store.get_open_positions()) == 1

    # Evaluate exit
    exits = evaluate_0dte_exits(
        positions=store.get_open_positions(),
        current_time_str="15:45",
        current_quotes={"SPY 2026-08-14 $500.00 CALL": 2.20},
    )
    assert len(exits) == 1

    # Execute exit
    res = execute_0dte_exits(exits, store=store)
    assert res["executed_count"] == 1
    assert len(store.get_open_positions()) == 0


# ---------------------------------------------------------------------------
# 5. AST Safety & Import Inertness Test
# ---------------------------------------------------------------------------

def test_zero_dte_engine_ast_safety():
    """
    Verifies that pilots/zero_dte_engine.py is pure compute and never imports
    heavy engines (CONSTRAINT #1 & #3).
    """
    engine_path = Path(__file__).resolve().parent.parent / "pilots" / "zero_dte_engine.py"
    assert engine_path.exists(), f"{engine_path} must exist"

    tree = ast.parse(engine_path.read_text(encoding="utf-8"), filename=str(engine_path))
    imported_modules = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module.split(".")[0])

    forbidden_modules = {
        "processing_engine",
        "strategy_engine",
        "forecasting_engine",
        "macro_engine",
        "technical_options_engine",
        "main_orchestrator",
        "desktop",
    }

    overlap = imported_modules & forbidden_modules
    assert not overlap, f"pilots/zero_dte_engine.py must not import {overlap}"


class TestManageZeroDteExits:
    """manage_0dte_exits -- closes audit finding F5
    (.claude/giant_master_plan_audit.md): evaluate_0dte_exits/execute_0dte_exits
    were correctly implemented and tested but had no live-callable composition
    path, unlike the sibling pilots/paper_broker.py::manage_position_exits.
    """

    @staticmethod
    def _today_et_symbol(underlying="SPY", strike=500.0, option_type="CALL"):
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
        return f"{underlying} {today} ${strike:.2f} {option_type}"

    @staticmethod
    def _far_future_symbol(underlying="QQQ", strike=400.0, option_type="PUT"):
        return f"{underlying} 2099-12-31 ${strike:.2f} {option_type}"

    def test_no_open_0dte_positions_returns_honest_empty_shape(self):
        mock_store = MagicMock()
        mock_pos = MagicMock(symbol=self._far_future_symbol())
        mock_store.get_open_positions.return_value = [mock_pos]

        result = manage_0dte_exits(store=mock_store)

        assert result["reason"] == "no_0dte_positions_open"
        assert result["signals"] == []
        assert result["executed_count"] == 0
        assert result["failed_count"] == 0

    def test_filters_out_non_0dte_expiring_position(self):
        mock_store = MagicMock()
        today_pos = MagicMock(symbol=self._today_et_symbol(), qty=1.0, avg_entry_price=2.0, market_value=200.0)
        future_pos = MagicMock(symbol=self._far_future_symbol(), qty=1.0, avg_entry_price=2.0, market_value=200.0)
        mock_store.get_open_positions.return_value = [today_pos, future_pos]

        with patch("pilots.zero_dte_engine.evaluate_0dte_exits", return_value=[]) as mock_eval:
            manage_0dte_exits(store=mock_store)

        # Only the today-expiring position should have been passed through to
        # the evaluator -- the far-future position must be filtered out.
        called_positions = mock_eval.call_args.kwargs["positions"]
        assert len(called_positions) == 1
        assert called_positions[0] is today_pos

    def test_dry_run_returns_signals_without_executing(self):
        mock_store = MagicMock()
        today_pos = MagicMock(symbol=self._today_et_symbol(), qty=1.0, avg_entry_price=2.0, market_value=200.0)
        mock_store.get_open_positions.return_value = [today_pos]

        fake_signal = ZeroDteExitSignal(
            exit_type="EXIT_HARD_TIME_STOP", exit_reason="HARD_TIME_STOP_1545",
            symbol=self._today_et_symbol(), contract_symbol=self._today_et_symbol(),
            position_id="pos_1", entry_price=2.0, current_price=2.5,
            pnl_pct=0.25, unrealized_pl=50.0, quantity=1.0, urgent=True,
        )
        with patch("pilots.zero_dte_engine.evaluate_0dte_exits", return_value=[fake_signal]):
            with patch("pilots.zero_dte_engine.execute_0dte_exits") as mock_execute:
                result = manage_0dte_exits(dry_run=True, store=mock_store)

        mock_execute.assert_not_called()
        assert result["reason"] == "dry_run"
        assert len(result["signals"]) == 1
        assert result["signals"][0]["exit_reason"] == "HARD_TIME_STOP_1545"
        assert result["executed_count"] == 0

    def test_non_dry_run_executes_and_merges_result(self):
        mock_store = MagicMock()
        today_pos = MagicMock(symbol=self._today_et_symbol(), qty=1.0, avg_entry_price=2.0, market_value=200.0)
        mock_store.get_open_positions.return_value = [today_pos]

        fake_signal = ZeroDteExitSignal(
            exit_type="EXIT_PROFIT_TARGET", exit_reason="PROFIT_TARGET_75",
            symbol=self._today_et_symbol(), contract_symbol=self._today_et_symbol(),
            position_id="pos_1", entry_price=2.0, current_price=3.5,
            pnl_pct=0.75, unrealized_pl=150.0, quantity=1.0, urgent=False,
        )
        fake_execute_result = {
            "executed_count": 1, "failed_count": 0,
            "executed": [{"order_id": "x", "position_symbol": self._today_et_symbol(), "exit_reason": "PROFIT_TARGET_75", "net_cash_impact": 150.0}],
            "failed": [],
        }
        with patch("pilots.zero_dte_engine.evaluate_0dte_exits", return_value=[fake_signal]):
            with patch("pilots.zero_dte_engine.execute_0dte_exits", return_value=fake_execute_result) as mock_execute:
                result = manage_0dte_exits(dry_run=False, store=mock_store)

        mock_execute.assert_called_once_with([fake_signal], store=mock_store)
        assert result["executed_count"] == 1
        assert result["executed"] == fake_execute_result["executed"]
        assert len(result["signals"]) == 1

    def test_no_exit_conditions_triggered_returns_honest_shape(self):
        mock_store = MagicMock()
        today_pos = MagicMock(symbol=self._today_et_symbol(), qty=1.0, avg_entry_price=2.0, market_value=200.0)
        mock_store.get_open_positions.return_value = [today_pos]

        with patch("pilots.zero_dte_engine.evaluate_0dte_exits", return_value=[]):
            with patch("pilots.zero_dte_engine.execute_0dte_exits") as mock_execute:
                result = manage_0dte_exits(store=mock_store)

        mock_execute.assert_not_called()
        assert result["reason"] == "no_exit_conditions_triggered"
        assert result["executed_count"] == 0

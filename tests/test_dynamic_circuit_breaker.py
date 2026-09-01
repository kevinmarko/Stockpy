"""
tests/test_dynamic_circuit_breaker.py
======================================
Comprehensive unit and integration tests for execution/dynamic_circuit_breaker.py.

Coverage
--------
1. Volatility Jump Detector:
   - Computes 5m EWMA realized vol vs 20d baseline.
   - Z-score > 3.5 triggers SOFT_HALT (VOLATILITY_BURST_HALT).
   - Tests series of returns, series of prices, direct volatility values.
   - Tests conservative vol_std fallback when missing/None.
2. Order Flow Imbalance (OFI) & Toxicity Crash Shield (VPIN):
   - Computes OFI = Δq_b - Δq_a and quote stream accumulation.
   - Computes VPIN across volume buckets in [0, 1].
   - OFI < -threshold AND VPIN > 0.40 triggers SOFT_HALT (FLASH_CRASH_SHIELD).
   - Counter-cases: OFI negative with low VPIN; high VPIN with positive OFI.
3. Intraday Loss Velocity Brake:
   - Computes d(PnL)/dt in $/minute.
   - Breach of -(Daily Loss Limit / 30 mins) triggers HARD_HALT (LOSS_VELOCITY_BREACH).
4. State Transitions & Metrics Persistence:
   - NORMAL, CAUTION, SOFT_HALT, HARD_HALT state transitions.
   - Atomic JSON persistence and recovery from disk.
   - Reset lifecycle and sentinel cleanup.
5. OrderIntent Evaluation:
   - SOFT_HALT blocks BUY (risk-increasing) and permits SELL (risk-reducing).
   - HARD_HALT blocks all orders.
   - NORMAL / CAUTION permits all orders.
6. Integration with PreTradeRiskGate (Check #0):
   - Injected DynamicCircuitBreaker instance.
   - Explicit RiskContext states.
   - File-backed GlobalKillSwitch soft-halt sentinel.
   - Alert dispatching on circuit breaker veto.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from execution.broker_base import (
    AccountSnapshot,
    OrderIntent,
    OrderSide,
    OrderType,
)
from execution.dynamic_circuit_breaker import (
    CircuitBreakerMetrics,
    CircuitBreakerState,
    DynamicCircuitBreaker,
    calculate_volatility_zscore_from_vols,
    compute_loss_velocity,
    compute_ofi,
    compute_ofi_from_quotes,
    compute_volatility_zscore,
    compute_vpin,
)
from execution.kill_switch import GlobalKillSwitch
from execution.risk_gate import PreTradeRiskGate, RiskContext


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

def _buy(symbol: str = "NVDA", qty: float = 10.0) -> OrderIntent:
    return OrderIntent(
        strategy_id="momentum",
        symbol=symbol,
        side=OrderSide.BUY,
        qty=qty,
        order_type=OrderType.MARKET,
    )


def _sell(symbol: str = "NVDA", qty: float = 10.0) -> OrderIntent:
    return OrderIntent(
        strategy_id="momentum",
        symbol=symbol,
        side=OrderSide.SELL,
        qty=qty,
        order_type=OrderType.MARKET,
    )


@pytest.fixture()
def tmp_ks(tmp_path: Path) -> GlobalKillSwitch:
    return GlobalKillSwitch(
        sentinel_file=tmp_path / "KILL_SWITCH",
        soft_halt_file=tmp_path / "SOFT_HALT",
    )


@pytest.fixture()
def tmp_cb(tmp_path: Path, tmp_ks: GlobalKillSwitch) -> DynamicCircuitBreaker:
    return DynamicCircuitBreaker(
        volatility_z_threshold=3.5,
        vpin_threshold=0.40,
        ofi_threshold=1000.0,
        loss_velocity_window_mins=30.0,
        daily_loss_limit_pct=0.02,
        state_file=tmp_path / "circuit_breaker_state.json",
        kill_switch=tmp_ks,
    )


# ---------------------------------------------------------------------------
# 1. Volatility Jump Detector Tests
# ---------------------------------------------------------------------------

class TestVolatilityJumpDetector:
    def test_direct_volatility_zscore_calculation(self):
        # Baseline = 0.01 (1%), vol_std = 0.002
        z = calculate_volatility_zscore_from_vols(
            realized_vol_5m=0.018,
            baseline_20d_vol=0.01,
            baseline_vol_std=0.002,
        )
        assert pytest.approx(z, 0.01) == 4.0

    def test_volatility_zscore_fallback_std(self):
        # Baseline = 0.02, missing std defaults to 15% of 0.02 = 0.003
        z = calculate_volatility_zscore_from_vols(
            realized_vol_5m=0.035,
            baseline_20d_vol=0.02,
            baseline_vol_std=None,
        )
        expected_std = 0.02 * 0.15  # 0.003
        expected_z = (0.035 - 0.02) / expected_std
        assert pytest.approx(z, 0.01) == expected_z

    def test_returns_series_ewma_volatility_zscore(self):
        rng = np.random.default_rng(42)
        # Normal 5m returns with std ~ 0.001
        normal_returns = rng.normal(0.0, 0.001, size=50)
        z_normal = compute_volatility_zscore(
            intraday_returns_or_prices=normal_returns,
            baseline_20d_vol=0.001,
            baseline_vol_std=0.0002,
        )
        assert abs(z_normal) < 3.0

        # Spike in returns with std ~ 0.005 (5x normal)
        spike_returns = np.concatenate([normal_returns[:40], rng.normal(0.0, 0.006, size=10)])
        z_spike = compute_volatility_zscore(
            intraday_returns_or_prices=spike_returns,
            baseline_20d_vol=0.001,
            baseline_vol_std=0.0002,
        )
        assert z_spike > 3.5

    def test_prices_series_volatility_zscore(self):
        # Generate price series that suddenly whipsaws
        prices = [100.0]
        for _ in range(30):
            prices.append(prices[-1] * (1.0 + np.random.normal(0, 0.001)))
        # Sudden large intraday jump/crash bars
        for r in [-0.03, 0.025, -0.04, 0.035, -0.05]:
            prices.append(prices[-1] * (1.0 + r))

        z = compute_volatility_zscore(
            intraday_returns_or_prices=prices,
            baseline_20d_vol=0.005,
            baseline_vol_std=0.001,
            is_prices=True,
        )
        assert z > 3.5

    def test_empty_or_single_element_returns_zero(self):
        assert compute_volatility_zscore([], baseline_20d_vol=0.01) == 0.0
        assert compute_volatility_zscore([0.01], baseline_20d_vol=0.01) == 0.0

    def test_cb_check_volatility_jump_trigger(self, tmp_cb: DynamicCircuitBreaker):
        # Normal
        triggered, z, reason = tmp_cb.check_volatility_jump(
            intraday_returns_or_prices=0.012,
            baseline_20d_vol=0.010,
            baseline_vol_std=0.002,
        )
        assert not triggered
        assert z == 1.0
        assert reason is None

        # Breach: Z = (0.020 - 0.010) / 0.002 = 5.0 > 3.5
        triggered, z, reason = tmp_cb.check_volatility_jump(
            intraday_returns_or_prices=0.020,
            baseline_20d_vol=0.010,
            baseline_vol_std=0.002,
        )
        assert triggered
        assert z == 5.0
        assert "VOLATILITY_BURST_HALT" in reason
        assert "Z-score 5.00 > threshold 3.50" in reason


# ---------------------------------------------------------------------------
# 2. OFI & VPIN Flash Crash Shield Tests
# ---------------------------------------------------------------------------

class TestFlashCrashShield:
    def test_compute_ofi_basic(self):
        # Bid increase 500, Ask increase 200 => OFI = +300
        assert compute_ofi(delta_bid_qty=500.0, delta_ask_qty=200.0) == 300.0
        # Bid decrease/cancel 100, Ask addition 1200 => OFI = -1300 (heavy selling)
        assert compute_ofi(delta_bid_qty=-100.0, delta_ask_qty=1200.0) == -1300.0

    def test_compute_ofi_from_quotes_series(self):
        bids = [(100.0, 500.0), (100.0, 400.0), (99.95, 300.0), (99.90, 200.0)]
        asks = [(100.05, 500.0), (100.05, 1000.0), (100.05, 2000.0), (100.00, 3000.0)]
        ofi = compute_ofi_from_quotes(bids, asks)
        assert ofi < 0.0  # Asks mounting, bids falling

    def test_compute_vpin_balanced_vs_toxic(self):
        # Balanced flow: buy=1000, sell=1000 => VPIN = 0.0
        buys = [1000.0, 1200.0, 950.0]
        sells = [1000.0, 1200.0, 950.0]
        assert compute_vpin(buys, sells) == 0.0

        # Highly toxic flow: buys=100, sells=900 across buckets
        toxic_buys = [100.0, 50.0, 80.0, 120.0]
        toxic_sells = [900.0, 950.0, 920.0, 880.0]
        vpin = compute_vpin(toxic_buys, toxic_sells)
        assert vpin > 0.80  # Extreme adverse selection

    def test_vpin_empty_or_mismatched(self):
        assert compute_vpin([], []) is None
        assert compute_vpin([100.0], [100.0, 200.0]) is None
        assert compute_vpin([0.0], [0.0]) is None

    def test_flash_crash_shield_trigger_conditions(self, tmp_cb: DynamicCircuitBreaker):
        # Triggered: OFI = -1500 (< -1000) and VPIN = 0.65 (> 0.40)
        triggered, reason = tmp_cb.check_flash_crash_shield(ofi=-1500.0, vpin=0.65)
        assert triggered
        assert "FLASH_CRASH_SHIELD" in reason
        assert "VPIN toxicity 0.65 > 0.40" in reason

        # Negative OFI but safe VPIN (0.25 <= 0.40) -> Not triggered
        triggered, reason = tmp_cb.check_flash_crash_shield(ofi=-1500.0, vpin=0.25)
        assert not triggered
        assert reason is None

        # Toxic VPIN (0.55) but positive OFI (buy demand) -> Not triggered
        triggered, reason = tmp_cb.check_flash_crash_shield(ofi=500.0, vpin=0.55)
        assert not triggered
        assert reason is None


# ---------------------------------------------------------------------------
# 3. Intraday Loss Velocity Brake Tests
# ---------------------------------------------------------------------------

class TestIntradayLossVelocityBrake:
    def test_compute_loss_velocity(self):
        # Lost $600 in 10 minutes => -$60/min
        vel = compute_loss_velocity(delta_pnl=-600.0, delta_minutes=10.0)
        assert pytest.approx(vel, 0.01) == -60.0

        # Zero minutes returns 0.0
        assert compute_loss_velocity(delta_pnl=-600.0, delta_minutes=0.0) == 0.0

    def test_loss_velocity_brake_threshold_and_trigger(self, tmp_cb: DynamicCircuitBreaker):
        # Account equity = $100,000. Daily limit = 2% = $2,000.
        # Window = 30 mins => Max allowed loss velocity = $2,000 / 30m = $66.67/min.
        # Threshold is -$66.67/min.

        # Safe: lost $300 in 10 mins = -$30/min > -$66.67/min
        breached, thresh, reason = tmp_cb.check_loss_velocity_brake(
            loss_velocity_per_min=-30.0,
            account_equity=100_000.0,
        )
        assert not breached
        assert pytest.approx(thresh, 0.01) == -66.67
        assert reason is None

        # Breach: lost $1,500 in 10 mins = -$150/min <= -$66.67/min
        breached, thresh, reason = tmp_cb.check_loss_velocity_brake(
            loss_velocity_per_min=-150.0,
            account_equity=100_000.0,
        )
        assert breached
        assert "LOSS_VELOCITY_BREACH" in reason
        assert "$150.00/min exceeds allowable rate $66.67/min" in reason


# ---------------------------------------------------------------------------
# 4. State Transitions & Metrics Persistence Tests
# ---------------------------------------------------------------------------

class TestDynamicCircuitBreakerStateTransitions:
    def test_normal_to_soft_halt_via_volatility(self, tmp_cb: DynamicCircuitBreaker):
        metrics = tmp_cb.update_metrics(volatility_zscore=4.2, persist=True)
        assert metrics.state == CircuitBreakerState.SOFT_HALT
        assert tmp_cb.current_state == CircuitBreakerState.SOFT_HALT
        assert tmp_cb.kill_switch.is_soft_halt_active()
        assert not tmp_cb.kill_switch.is_active()  # Hard kill switch is NOT active

    def test_normal_to_soft_halt_via_flash_crash(self, tmp_cb: DynamicCircuitBreaker):
        import settings
        with __import__('unittest.mock', fromlist=['mock']).patch.object(settings.settings, 'OFI_SHIELD_ENABLED', True, create=True):
            metrics = tmp_cb.update_metrics(ofi=-2000.0, vpin=0.55, persist=True)
            assert metrics.state == CircuitBreakerState.SOFT_HALT
        assert tmp_cb.kill_switch.is_soft_halt_active()

    def test_normal_to_hard_halt_via_loss_velocity(self, tmp_cb: DynamicCircuitBreaker):
        # Loss rate -$200/min on 100k equity (threshold -$66.67/min)
        metrics = tmp_cb.update_metrics(
            loss_velocity_per_min=-200.0,
            account_equity=100_000.0,
            persist=True,
        )
        assert metrics.state == CircuitBreakerState.HARD_HALT
        assert tmp_cb.current_state == CircuitBreakerState.HARD_HALT
        assert tmp_cb.kill_switch.is_active()  # Hard kill switch IS active

    def test_caution_state_transitions(self, tmp_cb: DynamicCircuitBreaker):
        metrics = tmp_cb.update_metrics(volatility_zscore=2.5, persist=True)
        assert metrics.state == CircuitBreakerState.CAUTION
        assert not tmp_cb.kill_switch.is_soft_halt_active()
        assert not tmp_cb.kill_switch.is_active()

    def test_atomic_persistence_and_loading(self, tmp_cb: DynamicCircuitBreaker, tmp_path: Path):
        tmp_cb.update_metrics(
            volatility_zscore=3.8,
            vpin=0.45,
            ofi=-1200.0,
            loss_velocity_per_min=-10.0,
            persist=True,
        )
        assert tmp_cb.state_file.exists()

        # Load into a fresh instance
        fresh_cb = DynamicCircuitBreaker(state_file=tmp_cb.state_file)
        loaded = fresh_cb.load_metrics()
        assert loaded is not None
        assert loaded.state == CircuitBreakerState.SOFT_HALT
        assert loaded.volatility_zscore == 3.8
        assert loaded.vpin == 0.45
        assert loaded.ofi == -1200.0

    def test_reset_clears_state_and_sentinels(self, tmp_cb: DynamicCircuitBreaker):
        tmp_cb.update_metrics(volatility_zscore=4.0, persist=True)
        assert tmp_cb.kill_switch.is_soft_halt_active()
        assert tmp_cb.state_file.exists()

        tmp_cb.reset()
        assert tmp_cb.current_state == CircuitBreakerState.NORMAL
        assert not tmp_cb.kill_switch.is_soft_halt_active()
        assert not tmp_cb.state_file.exists()


# ---------------------------------------------------------------------------
# 5. OrderIntent Evaluation Tests
# ---------------------------------------------------------------------------

class TestEvaluateOrderIntent:
    def test_normal_permits_both_buy_and_sell(self, tmp_cb: DynamicCircuitBreaker):
        tmp_cb.update_metrics(custom_state=CircuitBreakerState.NORMAL, persist=False)
        allowed, reason = tmp_cb.evaluate_order_intent(_buy("AAPL"))
        assert allowed
        assert reason is None

        allowed, reason = tmp_cb.evaluate_order_intent(_sell("AAPL"))
        assert allowed
        assert reason is None

    def test_caution_permits_both_buy_and_sell(self, tmp_cb: DynamicCircuitBreaker):
        tmp_cb.update_metrics(custom_state=CircuitBreakerState.CAUTION, persist=False)
        allowed, _ = tmp_cb.evaluate_order_intent(_buy("AAPL"))
        assert allowed
        allowed, _ = tmp_cb.evaluate_order_intent(_sell("AAPL"))
        assert allowed

    def test_soft_halt_asymmetric_gating(self, tmp_cb: DynamicCircuitBreaker):
        tmp_cb.update_metrics(
            custom_state=CircuitBreakerState.SOFT_HALT,
            custom_reason="VOLATILITY_BURST_HALT",
            persist=False,
        )
        # BUY blocked
        allowed, reason = tmp_cb.evaluate_order_intent(_buy("AAPL"))
        assert not allowed
        assert "SOFT_HALT active" in reason
        assert "risk-increasing BUY orders blocked" in reason

        # SELL permitted
        allowed, reason = tmp_cb.evaluate_order_intent(_sell("AAPL"))
        assert allowed
        assert "Permitted: risk-reducing SELL order allowed under SOFT_HALT" in reason

    def test_hard_halt_blocks_all(self, tmp_cb: DynamicCircuitBreaker):
        tmp_cb.update_metrics(
            custom_state=CircuitBreakerState.HARD_HALT,
            custom_reason="LOSS_VELOCITY_BREACH",
            persist=False,
        )
        buy_allowed, buy_reason = tmp_cb.evaluate_order_intent(_buy("AAPL"))
        assert not buy_allowed
        assert "HARD_HALT active" in buy_reason
        assert "all order submissions blocked" in buy_reason

        sell_allowed, sell_reason = tmp_cb.evaluate_order_intent(_sell("AAPL"))
        assert not sell_allowed
        assert "HARD_HALT active" in sell_reason
        assert "all order submissions blocked" in sell_reason


# ---------------------------------------------------------------------------
# 6. GlobalKillSwitch SoftHalt Integration Tests
# ---------------------------------------------------------------------------

class TestGlobalKillSwitchSoftHalt:
    def test_soft_halt_lifecycle(self, tmp_ks: GlobalKillSwitch):
        assert not tmp_ks.is_soft_halt_active()
        assert tmp_ks.soft_halt_reason() == ""

        tmp_ks.activate_soft_halt(reason="FLASH_CRASH_SHIELD triggered")
        assert tmp_ks.is_soft_halt_active()
        assert "FLASH_CRASH_SHIELD triggered" in tmp_ks.soft_halt_reason()

        tmp_ks.deactivate_soft_halt()
        assert not tmp_ks.is_soft_halt_active()
        assert tmp_ks.soft_halt_reason() == ""

    def test_soft_halt_alert_dispatch(self, tmp_ks: GlobalKillSwitch):
        with mock.patch("observability.alerts.send_alert") as m_alert:
            tmp_ks.activate_soft_halt(reason="Test soft halt alert")
        assert m_alert.called
        args, kwargs = m_alert.call_args
        assert args[0] == "WARNING"
        assert "Soft halt ACTIVATED" in args[1]
        assert kwargs.get("dedup_key") == "soft_halt_activate"


# ---------------------------------------------------------------------------
# 7. Risk Gate Check #0 Integration Tests
# ---------------------------------------------------------------------------

class TestRiskGateCheck0Integration:
    def _context(self) -> RiskContext:
        return RiskContext(
            account=AccountSnapshot(equity=100_000.0, cash=50_000.0, buying_power=50_000.0),
            current_prices={"NVDA": 120.0},
            start_of_day_equity=100_000.0,
            timestamp=datetime(2024, 1, 17, 17, 0, 0, tzinfo=timezone.utc),
        )

    def test_check_0_passes_under_normal_conditions(self):
        gate = PreTradeRiskGate()
        passed, results = gate.run_all(_buy("NVDA"), self._context())
        assert passed
        assert results[0].check_name == "dynamic_circuit_breaker"
        assert results[0].passed

    def test_check_0_blocks_buy_under_soft_halt(self, tmp_cb: DynamicCircuitBreaker):
        tmp_cb.update_metrics(volatility_zscore=4.5, persist=False)
        gate = PreTradeRiskGate(circuit_breaker=tmp_cb)

        passed, results = gate.run_all(_buy("NVDA"), self._context())
        assert not passed
        assert len(results) == 1
        assert results[0].check_name == "dynamic_circuit_breaker"
        assert not results[0].passed
        assert "SOFT_HALT active" in results[0].reason
        assert "BUY orders blocked" in results[0].reason

    def test_check_0_allows_sell_under_soft_halt(self, tmp_cb: DynamicCircuitBreaker):
        tmp_cb.update_metrics(volatility_zscore=4.5, persist=False)
        gate = PreTradeRiskGate(circuit_breaker=tmp_cb, enforce_market_hours=False)

        passed, results = gate.run_all(_sell("NVDA"), self._context())
        assert passed
        assert results[0].check_name == "dynamic_circuit_breaker"
        assert results[0].passed
        assert "SELL allowed" in results[0].reason

    def test_check_0_blocks_both_under_hard_halt(self, tmp_cb: DynamicCircuitBreaker):
        tmp_cb.update_metrics(
            loss_velocity_per_min=-200.0,
            account_equity=100_000.0,
            persist=False,
        )
        gate = PreTradeRiskGate(circuit_breaker=tmp_cb)

        buy_passed, buy_results = gate.run_all(_buy("NVDA"), self._context())
        assert not buy_passed
        assert not buy_results[0].passed
        assert "HARD_HALT active" in buy_results[0].reason

        sell_passed, sell_results = gate.run_all(_sell("NVDA"), self._context())
        assert not sell_passed
        assert not sell_results[0].passed
        assert "HARD_HALT active" in sell_results[0].reason

    def test_check_0_alerts_on_rejection(self):
        gate = PreTradeRiskGate()
        ctx = self._context()
        ctx.circuit_breaker_state = CircuitBreakerState.SOFT_HALT
        ctx.circuit_breaker_reason = "FLASH_CRASH_SHIELD"

        with mock.patch("observability.alerts.send_alert") as m_alert:
            passed, results = gate.run_all(_buy("NVDA"), ctx)
        assert not passed
        assert m_alert.called
        args, kwargs = m_alert.call_args
        assert args[0] == "WARNING"
        assert "Dynamic circuit breaker SOFT_HALT blocked order for NVDA" in args[1]

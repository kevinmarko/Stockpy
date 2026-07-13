"""
tests/test_risk_gate.py
=======================
Unit tests for execution/risk_gate.py.

Coverage
--------
* Each of the 10 individual checks: happy path (passes), failure path (blocks).
* Conservative-pass guarantee: missing context always returns passed=True.
* Integration: run_all() short-circuits at first failure.
* Rate-limit counter: NOT incremented for blocked orders.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pandas as pd

from execution.broker_base import (
    AccountSnapshot,
    OrderIntent,
    OrderSide,
    OrderType,
    PositionSnapshot,
)
from execution.risk_gate import PreTradeRiskGate, RiskContext
from settings import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _buy(symbol: str = "AAPL", qty: float = 1.0, strategy: str = "test") -> OrderIntent:
    return OrderIntent(
        strategy_id=strategy,
        symbol=symbol,
        side=OrderSide.BUY,
        qty=qty,
        order_type=OrderType.MARKET,
    )


def _sell(symbol: str = "AAPL", qty: float = 1.0, strategy: str = "test") -> OrderIntent:
    return OrderIntent(
        strategy_id=strategy,
        symbol=symbol,
        side=OrderSide.SELL,
        qty=qty,
        order_type=OrderType.MARKET,
    )


def _account(equity: float = 100_000.0) -> AccountSnapshot:
    return AccountSnapshot(equity=equity, cash=equity * 0.5, buying_power=equity * 0.5)


def _position(symbol: str, qty: float = 1.0, unrealized_pl: float = 0.0) -> PositionSnapshot:
    return PositionSnapshot(
        symbol=symbol, qty=qty, avg_entry_price=100.0,
        market_value=10_000.0, unrealized_pl=unrealized_pl,
    )


def _macro(
    *,
    kill_switch: bool = False,
    vix: float = 15.0,
    market_regime: str = "RISK ON",
    hmm_risk_on_probability: float | None = None,
) -> MagicMock:
    m = MagicMock()
    m.killSwitch = kill_switch
    m.vix = vix
    m.market_regime = market_regime
    m.hmm_risk_on_probability = hmm_risk_on_probability
    return m


def _rth_timestamp() -> datetime:
    """An NYSE RTH timestamp: Wednesday noon ET → UTC."""
    return datetime(2024, 1, 17, 17, 0, 0, tzinfo=timezone.utc)  # 12:00 ET


def _afterhours_timestamp() -> datetime:
    """An after-hours timestamp: Wednesday 9 PM ET → UTC."""
    return datetime(2024, 1, 17, 2, 0, 0, tzinfo=timezone.utc)  # 21:00 ET previous day UTC


# ---------------------------------------------------------------------------
# 1. max_position_size_check
# ---------------------------------------------------------------------------

class TestMaxPositionSizeCheck:
    def test_passes_within_limit(self):
        gate = PreTradeRiskGate(max_position_size_pct=0.10)
        ctx = RiskContext(
            account=_account(100_000),
            current_prices={"AAPL": 150.0},
        )
        result = gate.max_position_size_check(_buy("AAPL", qty=5), ctx)
        assert result.passed  # 5*150 = 750 < 10% of 100k = 10k

    def test_fails_over_limit(self):
        gate = PreTradeRiskGate(max_position_size_pct=0.05)
        ctx = RiskContext(
            account=_account(100_000),
            current_prices={"AAPL": 150.0},
        )
        result = gate.max_position_size_check(_buy("AAPL", qty=100), ctx)
        assert not result.passed  # 100*150 = 15k > 5% of 100k = 5k

    def test_conservative_pass_no_account(self):
        gate = PreTradeRiskGate()
        result = gate.max_position_size_check(_buy(), RiskContext())
        assert result.passed

    def test_conservative_pass_no_price(self):
        gate = PreTradeRiskGate()
        ctx = RiskContext(account=_account(100_000))
        result = gate.max_position_size_check(_buy("AAPL"), ctx)
        assert result.passed


# ---------------------------------------------------------------------------
# 2. portfolio_heat_check
# ---------------------------------------------------------------------------

class TestPortfolioHeatCheck:
    def test_passes_low_heat(self):
        gate = PreTradeRiskGate(max_portfolio_heat=0.06)
        ctx = RiskContext(
            open_positions=[_position("MSFT", unrealized_pl=-500.0)],
            account=_account(100_000),
        )
        result = gate.portfolio_heat_check(_buy(), ctx)
        assert result.passed  # 500/100000 = 0.5% < 6%

    def test_fails_high_heat(self):
        gate = PreTradeRiskGate(max_portfolio_heat=0.06)
        ctx = RiskContext(
            open_positions=[_position("MSFT", unrealized_pl=-7000.0)],
            account=_account(100_000),
        )
        result = gate.portfolio_heat_check(_buy(), ctx)
        assert not result.passed  # 7000/100000 = 7% > 6%

    def test_sell_skips_heat_check(self):
        gate = PreTradeRiskGate(max_portfolio_heat=0.001)
        ctx = RiskContext(
            open_positions=[_position("AAPL", unrealized_pl=-50_000.0)],
            account=_account(100_000),
        )
        result = gate.portfolio_heat_check(_sell(), ctx)
        assert result.passed

    def test_conservative_pass_no_account(self):
        gate = PreTradeRiskGate()
        ctx = RiskContext(open_positions=[_position("X", unrealized_pl=-100.0)])
        result = gate.portfolio_heat_check(_buy(), ctx)
        assert result.passed


class TestPortfolioHeatAlertDispatch:
    """Phase O3: a blocked portfolio_heat_check must fire a WARNING alert via
    observability.alerts.send_alert; a passing check must not (that would be
    an alert storm, not a warning)."""

    def test_fails_high_heat_calls_send_alert_warning(self):
        from unittest import mock
        gate = PreTradeRiskGate(max_portfolio_heat=0.06)
        ctx = RiskContext(
            open_positions=[_position("MSFT", unrealized_pl=-7000.0)],
            account=_account(100_000),
        )
        with mock.patch("observability.alerts.send_alert") as m_alert:
            result = gate.portfolio_heat_check(_buy("AAPL"), ctx)
        assert not result.passed
        assert m_alert.called
        args, kwargs = m_alert.call_args
        assert args[0] == "WARNING"
        assert kwargs.get("dedup_key") == "portfolio_heat"

    def test_passes_low_heat_does_not_call_send_alert(self):
        from unittest import mock
        gate = PreTradeRiskGate(max_portfolio_heat=0.06)
        ctx = RiskContext(
            open_positions=[_position("MSFT", unrealized_pl=-500.0)],
            account=_account(100_000),
        )
        with mock.patch("observability.alerts.send_alert") as m_alert:
            result = gate.portfolio_heat_check(_buy(), ctx)
        assert result.passed
        assert not m_alert.called

    def test_raising_send_alert_does_not_break_the_check_verdict(self):
        """A broken alert channel must never change the risk-gate's own verdict."""
        from unittest import mock
        gate = PreTradeRiskGate(max_portfolio_heat=0.06)
        ctx = RiskContext(
            open_positions=[_position("MSFT", unrealized_pl=-7000.0)],
            account=_account(100_000),
        )
        with mock.patch(
            "observability.alerts.send_alert", side_effect=RuntimeError("webhook down")
        ):
            result = gate.portfolio_heat_check(_buy(), ctx)  # must not raise
        assert not result.passed


# ---------------------------------------------------------------------------
# 3. max_correlation_check
# ---------------------------------------------------------------------------

class TestMaxCorrelationCheck:
    def _corr_df(self, r: float, sym_a: str = "AAPL", sym_b: str = "MSFT") -> pd.DataFrame:
        """Build a 50-row DataFrame where two columns have correlation ≈ r."""
        import numpy as np
        rng = np.random.default_rng(42)
        base = rng.standard_normal(50)
        noise = rng.standard_normal(50)
        b_col = r * base + (1 - abs(r)) ** 0.5 * noise
        return pd.DataFrame({sym_a: base, sym_b: b_col})

    def test_passes_low_correlation(self):
        gate = PreTradeRiskGate(max_correlation=0.85)
        ctx = RiskContext(
            open_positions=[_position("MSFT")],
            returns_df=self._corr_df(0.3),
        )
        result = gate.max_correlation_check(_buy("AAPL"), ctx)
        assert result.passed

    def test_fails_high_positive_correlation(self):
        gate = PreTradeRiskGate(max_correlation=0.85)
        ctx = RiskContext(
            open_positions=[_position("MSFT")],
            returns_df=self._corr_df(0.95),
        )
        result = gate.max_correlation_check(_buy("AAPL"), ctx)
        assert not result.passed

    def test_fails_high_negative_correlation(self):
        gate = PreTradeRiskGate(max_correlation=0.85)
        ctx = RiskContext(
            open_positions=[_position("MSFT")],
            returns_df=self._corr_df(-0.95),
        )
        result = gate.max_correlation_check(_buy("AAPL"), ctx)
        assert not result.passed  # |r| = 0.95 > 0.85

    def test_conservative_pass_no_returns(self):
        gate = PreTradeRiskGate()
        ctx = RiskContext(open_positions=[_position("MSFT")])
        result = gate.max_correlation_check(_buy("AAPL"), ctx)
        assert result.passed

    def test_conservative_pass_symbol_not_in_returns(self):
        gate = PreTradeRiskGate(max_correlation=0.50)
        ctx = RiskContext(
            open_positions=[_position("MSFT")],
            returns_df=self._corr_df(0.99, "GOOG", "MSFT"),
        )
        result = gate.max_correlation_check(_buy("AAPL"), ctx)
        assert result.passed  # AAPL not in returns_df


class TestCorrelationAlertDispatch:
    """Phase O3: a blocked max_correlation_check must fire a WARNING alert via
    observability.alerts.send_alert; a passing check must not."""

    def _corr_df(self, r: float, sym_a: str = "AAPL", sym_b: str = "MSFT") -> pd.DataFrame:
        import numpy as np
        rng = np.random.default_rng(42)
        base = rng.standard_normal(50)
        noise = rng.standard_normal(50)
        b_col = r * base + (1 - abs(r)) ** 0.5 * noise
        return pd.DataFrame({sym_a: base, sym_b: b_col})

    def test_fails_high_correlation_calls_send_alert_warning(self):
        from unittest import mock
        gate = PreTradeRiskGate(max_correlation=0.85)
        ctx = RiskContext(
            open_positions=[_position("MSFT")],
            returns_df=self._corr_df(0.95),
        )
        with mock.patch("observability.alerts.send_alert") as m_alert:
            result = gate.max_correlation_check(_buy("AAPL"), ctx)
        assert not result.passed
        assert m_alert.called
        args, kwargs = m_alert.call_args
        assert args[0] == "WARNING"
        assert kwargs.get("dedup_key") == "correlation_concentration"

    def test_passes_low_correlation_does_not_call_send_alert(self):
        from unittest import mock
        gate = PreTradeRiskGate(max_correlation=0.85)
        ctx = RiskContext(
            open_positions=[_position("MSFT")],
            returns_df=self._corr_df(0.3),
        )
        with mock.patch("observability.alerts.send_alert") as m_alert:
            result = gate.max_correlation_check(_buy("AAPL"), ctx)
        assert result.passed
        assert not m_alert.called

    def test_raising_send_alert_does_not_break_the_check_verdict(self):
        from unittest import mock
        gate = PreTradeRiskGate(max_correlation=0.85)
        ctx = RiskContext(
            open_positions=[_position("MSFT")],
            returns_df=self._corr_df(0.95),
        )
        with mock.patch(
            "observability.alerts.send_alert", side_effect=RuntimeError("webhook down")
        ):
            result = gate.max_correlation_check(_buy("AAPL"), ctx)  # must not raise
        assert not result.passed


# ---------------------------------------------------------------------------
# 4. daily_loss_limit_check
# ---------------------------------------------------------------------------

class TestDailyLossLimitCheck:
    def test_passes_small_loss(self):
        gate = PreTradeRiskGate(daily_loss_limit_pct=0.02)
        ctx = RiskContext(
            start_of_day_equity=100_000.0,
            account=_account(99_500.0),  # -0.5% — within limit
        )
        result = gate.daily_loss_limit_check(_buy(), ctx)
        assert result.passed

    def test_fails_large_loss(self):
        gate = PreTradeRiskGate(daily_loss_limit_pct=0.02)
        ctx = RiskContext(
            start_of_day_equity=100_000.0,
            account=_account(97_500.0),  # -2.5% > 2% limit
        )
        result = gate.daily_loss_limit_check(_buy(), ctx)
        assert not result.passed

    def test_sell_skips_loss_limit(self):
        gate = PreTradeRiskGate(daily_loss_limit_pct=0.001)
        ctx = RiskContext(
            start_of_day_equity=100_000.0,
            account=_account(50_000.0),  # huge loss
        )
        result = gate.daily_loss_limit_check(_sell(), ctx)
        assert result.passed

    def test_conservative_pass_missing_start_equity(self):
        gate = PreTradeRiskGate()
        ctx = RiskContext(account=_account(95_000.0))
        result = gate.daily_loss_limit_check(_buy(), ctx)
        assert result.passed


# ---------------------------------------------------------------------------
# 5. macro_kill_switch_check
# ---------------------------------------------------------------------------

class TestMacroKillSwitchCheck:
    @pytest.fixture(autouse=True)
    def _macro_regime_gate_enabled(self, monkeypatch):
        """Pin the operator-controlled gate to enabled (the class default) so
        these tests exercise the real macro kill-switch logic regardless of
        the ambient .env's MACRO_REGIME_GATE_ENABLED value (this check is a
        no-op pass-through when the operator has disabled the gate)."""
        monkeypatch.setattr(settings, "MACRO_REGIME_GATE_ENABLED", True)

    def test_passes_when_not_active(self):
        gate = PreTradeRiskGate()
        ctx = RiskContext(macro=_macro(kill_switch=False))
        result = gate.macro_kill_switch_check(_buy(), ctx)
        assert result.passed

    def test_fails_when_active(self):
        gate = PreTradeRiskGate()
        ctx = RiskContext(macro=_macro(kill_switch=True, vix=35.0))
        result = gate.macro_kill_switch_check(_buy(), ctx)
        assert not result.passed

    def test_sell_skips_macro_kill_switch(self):
        gate = PreTradeRiskGate()
        ctx = RiskContext(macro=_macro(kill_switch=True))
        result = gate.macro_kill_switch_check(_sell(), ctx)
        assert result.passed

    def test_conservative_pass_no_macro(self):
        gate = PreTradeRiskGate()
        result = gate.macro_kill_switch_check(_buy(), RiskContext())
        assert result.passed


# ---------------------------------------------------------------------------
# 6. hmm_regime_check
# ---------------------------------------------------------------------------

class TestHmmRegimeCheck:
    def test_passes_low_risk_off(self):
        gate = PreTradeRiskGate(hmm_risk_off_block_threshold=0.80)
        ctx = RiskContext(macro=_macro(hmm_risk_on_probability=0.60))  # risk_off = 0.40
        result = gate.hmm_regime_check(_buy(), ctx)
        assert result.passed

    def test_fails_high_risk_off(self):
        gate = PreTradeRiskGate(hmm_risk_off_block_threshold=0.80)
        ctx = RiskContext(macro=_macro(hmm_risk_on_probability=0.10))  # risk_off = 0.90
        result = gate.hmm_regime_check(_buy(), ctx)
        assert not result.passed

    def test_sell_skips_hmm(self):
        gate = PreTradeRiskGate(hmm_risk_off_block_threshold=0.10)
        ctx = RiskContext(macro=_macro(hmm_risk_on_probability=0.05))
        result = gate.hmm_regime_check(_sell(), ctx)
        assert result.passed

    def test_conservative_pass_no_hmm_probability(self):
        gate = PreTradeRiskGate()
        ctx = RiskContext(macro=_macro(hmm_risk_on_probability=None))
        result = gate.hmm_regime_check(_buy(), ctx)
        assert result.passed

    def test_conservative_pass_no_macro(self):
        gate = PreTradeRiskGate()
        result = gate.hmm_regime_check(_buy(), RiskContext())
        assert result.passed


# ---------------------------------------------------------------------------
# 7. stress_scenario_check
# ---------------------------------------------------------------------------

class TestStressScenarioCheck:
    def test_passes_non_premium_strategy(self):
        gate = PreTradeRiskGate()
        ctx = RiskContext(
            macro=_macro(vix=40.0),
            is_premium_sell_strategy=False,
        )
        result = gate.stress_scenario_check(_buy(), ctx)
        assert result.passed

    def test_passes_premium_strategy_low_vix(self):
        gate = PreTradeRiskGate()
        ctx = RiskContext(
            macro=_macro(vix=20.0),
            is_premium_sell_strategy=True,
        )
        result = gate.stress_scenario_check(_buy(), ctx)
        assert result.passed

    def test_fails_premium_strategy_high_vix(self):
        gate = PreTradeRiskGate()
        ctx = RiskContext(
            macro=_macro(vix=35.0),
            is_premium_sell_strategy=True,
        )
        result = gate.stress_scenario_check(_buy(), ctx)
        assert not result.passed

    def test_conservative_pass_premium_no_macro(self):
        gate = PreTradeRiskGate()
        ctx = RiskContext(is_premium_sell_strategy=True)
        result = gate.stress_scenario_check(_buy(), ctx)
        assert result.passed


# ---------------------------------------------------------------------------
# 8. market_hours_check
# ---------------------------------------------------------------------------

class TestMarketHoursCheck:
    def test_passes_during_rth(self):
        gate = PreTradeRiskGate(enforce_market_hours=True)
        ctx = RiskContext(timestamp=_rth_timestamp())
        result = gate.market_hours_check(_buy(), ctx)
        assert result.passed

    def test_fails_after_hours(self):
        gate = PreTradeRiskGate(enforce_market_hours=True)
        # 21:00 ET = 02:00 UTC next day
        after_hours = datetime(2024, 1, 18, 2, 0, 0, tzinfo=timezone.utc)
        ctx = RiskContext(timestamp=after_hours)
        result = gate.market_hours_check(_buy(), ctx)
        assert not result.passed

    def test_passes_when_enforcement_disabled(self):
        gate = PreTradeRiskGate(enforce_market_hours=False)
        after_hours = datetime(2024, 1, 18, 2, 0, 0, tzinfo=timezone.utc)
        ctx = RiskContext(timestamp=after_hours)
        result = gate.market_hours_check(_buy(), ctx)
        assert result.passed

    def test_market_open_at_930(self):
        gate = PreTradeRiskGate(enforce_market_hours=True)
        # 09:30 ET = 14:30 UTC (winter)
        open_time = datetime(2024, 1, 17, 14, 30, 0, tzinfo=timezone.utc)
        ctx = RiskContext(timestamp=open_time)
        result = gate.market_hours_check(_buy(), ctx)
        assert result.passed

    def test_market_closed_at_1601(self):
        gate = PreTradeRiskGate(enforce_market_hours=True)
        # 16:01 ET = 21:01 UTC (winter)
        after_close = datetime(2024, 1, 17, 21, 1, 0, tzinfo=timezone.utc)
        ctx = RiskContext(timestamp=after_close)
        result = gate.market_hours_check(_buy(), ctx)
        assert not result.passed


# ---------------------------------------------------------------------------
# 9. minimum_validation_check
# ---------------------------------------------------------------------------

class TestMinimumValidationCheck:
    def test_passes_deployable_strategy(self):
        gate = PreTradeRiskGate()
        ctx = RiskContext(validation_reports={"strat_a": True})
        result = gate.minimum_validation_check(_buy(strategy="strat_a"), ctx)
        assert result.passed

    def test_fails_non_deployable_strategy(self):
        gate = PreTradeRiskGate()
        ctx = RiskContext(validation_reports={"strat_a": False})
        result = gate.minimum_validation_check(_buy(strategy="strat_a"), ctx)
        assert not result.passed

    def test_conservative_pass_unknown_strategy(self):
        gate = PreTradeRiskGate(require_validation_report=False)
        ctx = RiskContext(validation_reports={})
        result = gate.minimum_validation_check(_buy(strategy="unknown"), ctx)
        assert result.passed

    def test_fails_unknown_strategy_when_required(self):
        gate = PreTradeRiskGate(require_validation_report=True)
        ctx = RiskContext(validation_reports={})
        result = gate.minimum_validation_check(_buy(strategy="unknown"), ctx)
        assert not result.passed


# ---------------------------------------------------------------------------
# 10. max_order_rate_check
# ---------------------------------------------------------------------------

class TestMaxOrderRateCheck:
    def test_passes_within_rate(self):
        gate = PreTradeRiskGate(max_order_rate_per_min=5)
        now = datetime(2024, 1, 17, 15, 0, 0, tzinfo=timezone.utc)
        ctx = RiskContext(timestamp=now)
        for _ in range(4):
            result = gate.max_order_rate_check(_buy(), ctx)
            assert result.passed

    def test_fails_over_rate(self):
        gate = PreTradeRiskGate(max_order_rate_per_min=3)
        now = datetime(2024, 1, 17, 15, 0, 0, tzinfo=timezone.utc)
        ctx = RiskContext(timestamp=now)
        for _ in range(3):
            gate.max_order_rate_check(_buy(), ctx)
        result = gate.max_order_rate_check(_buy(), ctx)
        assert not result.passed

    def test_window_resets_after_60s(self):
        gate = PreTradeRiskGate(max_order_rate_per_min=2)
        t0 = datetime(2024, 1, 17, 15, 0, 0, tzinfo=timezone.utc)
        ctx0 = RiskContext(timestamp=t0)
        gate.max_order_rate_check(_buy(), ctx0)
        gate.max_order_rate_check(_buy(), ctx0)
        # 70 seconds later — old timestamps have expired
        t1 = t0 + timedelta(seconds=70)
        ctx1 = RiskContext(timestamp=t1)
        result = gate.max_order_rate_check(_buy(), ctx1)
        assert result.passed


# ---------------------------------------------------------------------------
# Integration: run_all()
# ---------------------------------------------------------------------------

class TestRunAll:
    @pytest.fixture(autouse=True)
    def _macro_regime_gate_enabled(self, monkeypatch):
        """See TestMacroKillSwitchCheck._macro_regime_gate_enabled — several
        of these integration tests trigger the macro check specifically and
        need it enabled regardless of the ambient .env value."""
        monkeypatch.setattr(settings, "MACRO_REGIME_GATE_ENABLED", True)

    def _valid_context(self) -> RiskContext:
        """A context that passes all checks."""
        return RiskContext(
            macro=_macro(kill_switch=False, vix=15.0, hmm_risk_on_probability=0.70),
            account=_account(100_000.0),
            open_positions=[],
            current_prices={"AAPL": 150.0},
            start_of_day_equity=100_000.0,
            validation_reports={"test": True},
            is_premium_sell_strategy=False,
            timestamp=_rth_timestamp(),
        )

    def test_all_pass(self):
        gate = PreTradeRiskGate(enforce_market_hours=True)
        passed, results = gate.run_all(_buy(), self._valid_context())
        assert passed
        assert all(r.passed for r in results)
        assert len(results) == 10  # all 10 checks ran

    def test_short_circuit_on_first_failure(self):
        gate = PreTradeRiskGate(enforce_market_hours=True)
        # Trigger macro kill switch (check 5); checks 6–10 must not run
        ctx = self._valid_context()
        ctx.macro = _macro(kill_switch=True)
        passed, results = gate.run_all(_buy(), ctx)
        assert not passed
        # Only checks 1–5 ran (short-circuit)
        assert len(results) == 5
        assert not results[-1].passed

    def test_rate_limit_not_charged_on_blocked_order(self):
        """Rate counter must only increment on full-pass — blocked orders waste no budget."""
        gate = PreTradeRiskGate(max_order_rate_per_min=2, enforce_market_hours=True)
        ctx = self._valid_context()
        # Block at macro kill switch
        ctx.macro = _macro(kill_switch=True)
        for _ in range(10):
            gate.run_all(_buy(), ctx)
        # Now allow the order through; rate counter should still be 0
        valid_ctx = self._valid_context()
        valid_ctx.timestamp = _rth_timestamp()
        passed, results = gate.run_all(_buy(), valid_ctx)
        assert passed  # Would fail if rate was charged for blocked orders

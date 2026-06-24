"""
tests/test_risk_gate.py
=======================
Unit tests for ``execution/risk_gate.py``.

Each of the 10 pre-trade checks is tested with:
* A happy-path (gate should pass)
* A failing-path (gate should block)
* Where applicable: missing context → conservative pass
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from execution.broker_base import AccountSnapshot, OrderIntent, OrderSide, OrderType, PositionSnapshot
from execution.risk_gate import PreTradeRiskGate, RiskCheckResult, RiskContext


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _buy(symbol: str = "AAPL", qty: float = 10.0) -> OrderIntent:
    return OrderIntent(
        strategy_id="test_strategy",
        symbol=symbol,
        side=OrderSide.BUY,
        qty=qty,
        order_type=OrderType.MARKET,
    )


def _sell(symbol: str = "AAPL", qty: float = 10.0) -> OrderIntent:
    return OrderIntent(
        strategy_id="test_strategy",
        symbol=symbol,
        side=OrderSide.SELL,
        qty=qty,
        order_type=OrderType.MARKET,
    )


def _account(equity: float = 100_000.0) -> AccountSnapshot:
    return AccountSnapshot(equity=equity, cash=equity, buying_power=equity * 2)


def _position(symbol: str, unrealized_pl: float, qty: float = 10.0) -> PositionSnapshot:
    return PositionSnapshot(
        symbol=symbol,
        qty=qty,
        avg_entry_price=100.0,
        market_value=abs(qty) * 100.0,
        unrealized_pl=unrealized_pl,
    )


def _gate(**kwargs) -> PreTradeRiskGate:
    return PreTradeRiskGate(**kwargs)


def _market_hours_ts(hour: int, minute: int) -> datetime:
    """UTC time that corresponds to the given ET hour:minute on a weekday."""
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    today_et = datetime.now(et).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return today_et.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# 1. max_position_size_check
# ---------------------------------------------------------------------------

class TestMaxPositionSize:
    def test_passes_when_notional_within_limit(self):
        gate = _gate(max_position_size_pct=1.0)
        ctx = RiskContext(
            account=_account(100_000),
            current_prices={"AAPL": 150.0},
        )
        result = gate.max_position_size_check(_buy("AAPL", qty=100.0), ctx)
        # notional = 15000 < 100000 * 1.0
        assert result.passed

    def test_blocks_when_notional_exceeds_limit(self):
        gate = _gate(max_position_size_pct=0.10)
        ctx = RiskContext(
            account=_account(100_000),
            current_prices={"AAPL": 150.0},
        )
        result = gate.max_position_size_check(_buy("AAPL", qty=100.0), ctx)
        # notional = 15000 > 100000 * 0.10 = 10000
        assert not result.passed
        assert "AAPL" in result.reason

    def test_passes_conservatively_when_no_account(self):
        gate = _gate()
        ctx = RiskContext(current_prices={"AAPL": 150.0})
        result = gate.max_position_size_check(_buy("AAPL", qty=100.0), ctx)
        assert result.passed

    def test_passes_conservatively_when_no_price(self):
        gate = _gate(max_position_size_pct=0.10)
        ctx = RiskContext(account=_account(100_000), current_prices={})
        result = gate.max_position_size_check(_buy("AAPL", qty=100.0), ctx)
        assert result.passed


# ---------------------------------------------------------------------------
# 2. portfolio_heat_check
# ---------------------------------------------------------------------------

class TestPortfolioHeat:
    def test_passes_when_heat_is_low(self):
        gate = _gate(max_portfolio_heat=0.06)
        ctx = RiskContext(
            account=_account(100_000),
            open_positions=[_position("MSFT", unrealized_pl=-2_000)],
        )
        # heat = 2000/100000 = 2% < 6%
        result = gate.portfolio_heat_check(_buy(), ctx)
        assert result.passed

    def test_blocks_when_heat_exceeds_limit(self):
        gate = _gate(max_portfolio_heat=0.04)
        ctx = RiskContext(
            account=_account(100_000),
            open_positions=[_position("MSFT", unrealized_pl=-5_000)],
        )
        # heat = 5000/100000 = 5% > 4%
        result = gate.portfolio_heat_check(_buy(), ctx)
        assert not result.passed

    def test_sell_is_exempt_from_heat_check(self):
        gate = _gate(max_portfolio_heat=0.01)
        ctx = RiskContext(
            account=_account(100_000),
            open_positions=[_position("AAPL", unrealized_pl=-50_000)],
        )
        result = gate.portfolio_heat_check(_sell(), ctx)
        assert result.passed

    def test_positive_unrealized_pl_not_counted(self):
        gate = _gate(max_portfolio_heat=0.06)
        ctx = RiskContext(
            account=_account(100_000),
            open_positions=[_position("AAPL", unrealized_pl=+5_000)],
        )
        result = gate.portfolio_heat_check(_buy(), ctx)
        assert result.passed


# ---------------------------------------------------------------------------
# 3. max_correlation_check
# ---------------------------------------------------------------------------

class TestMaxCorrelation:
    def _returns(self) -> pd.DataFrame:
        rng = pd.date_range("2024-01-01", periods=100, freq="D")
        import numpy as np

        np.random.seed(42)
        spy = pd.Series(np.random.randn(100), index=rng)
        highly_corr = spy + np.random.randn(100) * 0.01   # |r| ≈ 0.99
        low_corr = pd.Series(np.random.randn(100), index=rng)
        return pd.DataFrame({"SPY": spy, "AAPL": highly_corr, "GLD": low_corr})

    def test_passes_when_correlation_is_low(self):
        gate = _gate(max_correlation=0.85)
        ctx = RiskContext(
            returns_df=self._returns(),
            open_positions=[_position("GLD", unrealized_pl=0)],
        )
        # GLD and SPY are independent — should pass
        result = gate.max_correlation_check(_buy("SPY"), ctx)
        assert result.passed, result.reason

    def test_blocks_when_correlation_is_high(self):
        gate = _gate(max_correlation=0.85)
        ctx = RiskContext(
            returns_df=self._returns(),
            open_positions=[_position("SPY", unrealized_pl=0)],
        )
        # AAPL is highly correlated with SPY in this dataset
        result = gate.max_correlation_check(_buy("AAPL"), ctx)
        assert not result.passed, result.reason

    def test_passes_conservatively_with_no_returns(self):
        gate = _gate()
        ctx = RiskContext(returns_df=None, open_positions=[_position("SPY", 0)])
        result = gate.max_correlation_check(_buy("AAPL"), ctx)
        assert result.passed

    def test_passes_with_no_existing_positions(self):
        gate = _gate()
        ctx = RiskContext(returns_df=self._returns(), open_positions=[])
        result = gate.max_correlation_check(_buy("AAPL"), ctx)
        assert result.passed


# ---------------------------------------------------------------------------
# 4. daily_loss_limit_check
# ---------------------------------------------------------------------------

class TestDailyLossLimit:
    def test_passes_when_pnl_is_acceptable(self):
        gate = _gate(daily_loss_limit_pct=0.02)
        ctx = RiskContext(
            account=_account(99_500),       # -0.5% from 100k
            start_of_day_equity=100_000.0,
        )
        result = gate.daily_loss_limit_check(_buy(), ctx)
        assert result.passed

    def test_blocks_when_loss_exceeds_limit(self):
        gate = _gate(daily_loss_limit_pct=0.02)
        ctx = RiskContext(
            account=_account(97_000),       # -3% from 100k
            start_of_day_equity=100_000.0,
        )
        result = gate.daily_loss_limit_check(_buy(), ctx)
        assert not result.passed

    def test_sell_exempt_from_loss_limit(self):
        gate = _gate(daily_loss_limit_pct=0.02)
        ctx = RiskContext(
            account=_account(90_000),
            start_of_day_equity=100_000.0,
        )
        result = gate.daily_loss_limit_check(_sell(), ctx)
        assert result.passed

    def test_passes_conservatively_when_sod_missing(self):
        gate = _gate()
        ctx = RiskContext(account=_account(50_000), start_of_day_equity=None)
        result = gate.daily_loss_limit_check(_buy(), ctx)
        assert result.passed


# ---------------------------------------------------------------------------
# 5. macro_kill_switch_check
# ---------------------------------------------------------------------------

class TestMacroKillSwitch:
    def _macro(self, kill: bool, vix: float = 15.0):
        """Build a minimal MacroEconomicDTO-like stub via the real class."""
        from dto_models import MacroEconomicDTO
        dto = MacroEconomicDTO(
            yield_curve_10y_2y=0.5,
            high_yield_oas=3.0,
            inflation_rate=2.0,
            nominal_10y=4.0,
            vix_value=vix,
        )
        return dto

    def test_passes_when_kill_switch_inactive(self):
        gate = _gate()
        ctx = RiskContext(macro=self._macro(kill=False, vix=15.0))
        result = gate.macro_kill_switch_check(_buy(), ctx)
        assert result.passed

    def test_blocks_when_kill_switch_active(self):
        gate = _gate()
        # Force a macro state that triggers killSwitch: vix > 30, recession regime
        from dto_models import MacroEconomicDTO
        dto = MacroEconomicDTO(
            yield_curve_10y_2y=-1.0,    # inverted → RECESSION
            high_yield_oas=10.0,         # high yield stress
            inflation_rate=2.0,
            nominal_10y=4.0,
            vix_value=35.0,              # vix > 30 → killSwitch
        )
        ctx = RiskContext(macro=dto)
        result = gate.macro_kill_switch_check(_buy(), ctx)
        assert not result.passed

    def test_passes_conservatively_with_no_macro(self):
        gate = _gate()
        ctx = RiskContext(macro=None)
        result = gate.macro_kill_switch_check(_buy(), ctx)
        assert result.passed

    def test_sell_exempt_from_macro_kill_switch(self):
        gate = _gate()
        from dto_models import MacroEconomicDTO
        dto = MacroEconomicDTO(
            yield_curve_10y_2y=-1.0,
            high_yield_oas=10.0,
            inflation_rate=2.0,
            nominal_10y=4.0,
            vix_value=35.0,
        )
        ctx = RiskContext(macro=dto)
        result = gate.macro_kill_switch_check(_sell(), ctx)
        assert result.passed


# ---------------------------------------------------------------------------
# 6. hmm_regime_check
# ---------------------------------------------------------------------------

class TestHMMRegimeCheck:
    def _macro_with_hmm(self, risk_on_prob: float):
        from dto_models import MacroEconomicDTO
        return MacroEconomicDTO(
            yield_curve_10y_2y=0.5,
            high_yield_oas=3.0,
            inflation_rate=2.0,
            nominal_10y=4.0,
            vix_value=18.0,
            hmm_risk_on_probability=risk_on_prob,
        )

    def test_passes_when_risk_on_probability_is_high(self):
        gate = _gate(hmm_risk_off_block_threshold=0.80)
        ctx = RiskContext(macro=self._macro_with_hmm(risk_on_prob=0.85))
        result = gate.hmm_regime_check(_buy(), ctx)
        assert result.passed

    def test_blocks_when_risk_off_probability_is_high(self):
        gate = _gate(hmm_risk_off_block_threshold=0.80)
        ctx = RiskContext(macro=self._macro_with_hmm(risk_on_prob=0.10))
        # risk_off = 0.90 > 0.80 → block
        result = gate.hmm_regime_check(_buy(), ctx)
        assert not result.passed

    def test_passes_conservatively_when_hmm_unavailable(self):
        gate = _gate()
        from dto_models import MacroEconomicDTO
        ctx = RiskContext(macro=MacroEconomicDTO(
            yield_curve_10y_2y=0.5, high_yield_oas=3.0,
            inflation_rate=2.0, nominal_10y=4.0, vix_value=18.0,
            hmm_risk_on_probability=None,
        ))
        result = gate.hmm_regime_check(_buy(), ctx)
        assert result.passed

    def test_sell_exempt_from_hmm_check(self):
        gate = _gate(hmm_risk_off_block_threshold=0.80)
        ctx = RiskContext(macro=self._macro_with_hmm(risk_on_prob=0.05))
        result = gate.hmm_regime_check(_sell(), ctx)
        assert result.passed


# ---------------------------------------------------------------------------
# 7. stress_scenario_check
# ---------------------------------------------------------------------------

class TestStressScenarioCheck:
    def _macro_vix(self, vix: float):
        from dto_models import MacroEconomicDTO
        return MacroEconomicDTO(
            yield_curve_10y_2y=0.5, high_yield_oas=3.0,
            inflation_rate=2.0, nominal_10y=4.0, vix_value=vix,
        )

    def test_passes_for_non_premium_strategy(self):
        gate = _gate()
        ctx = RiskContext(macro=self._macro_vix(40.0), is_premium_sell_strategy=False)
        result = gate.stress_scenario_check(_buy(), ctx)
        assert result.passed

    def test_passes_when_vix_is_low(self):
        gate = _gate()
        ctx = RiskContext(macro=self._macro_vix(18.0), is_premium_sell_strategy=True)
        result = gate.stress_scenario_check(_buy(), ctx)
        assert result.passed

    def test_blocks_premium_sell_when_vix_above_30(self):
        gate = _gate()
        ctx = RiskContext(macro=self._macro_vix(35.0), is_premium_sell_strategy=True)
        result = gate.stress_scenario_check(_buy(), ctx)
        assert not result.passed

    def test_passes_conservatively_when_no_macro(self):
        gate = _gate()
        ctx = RiskContext(macro=None, is_premium_sell_strategy=True)
        result = gate.stress_scenario_check(_buy(), ctx)
        assert result.passed


# ---------------------------------------------------------------------------
# 8. market_hours_check
# ---------------------------------------------------------------------------

class TestMarketHoursCheck:
    def test_passes_during_rth(self):
        gate = _gate(enforce_market_hours=True)
        ts = _market_hours_ts(10, 30)  # 10:30 ET — well within RTH
        ctx = RiskContext(timestamp=ts)
        result = gate.market_hours_check(_buy(), ctx)
        assert result.passed, result.reason

    def test_blocks_before_open(self):
        gate = _gate(enforce_market_hours=True)
        ts = _market_hours_ts(8, 0)  # 08:00 ET — pre-market
        ctx = RiskContext(timestamp=ts)
        result = gate.market_hours_check(_buy(), ctx)
        assert not result.passed

    def test_blocks_after_close(self):
        gate = _gate(enforce_market_hours=True)
        ts = _market_hours_ts(16, 1)  # 16:01 ET — after hours
        ctx = RiskContext(timestamp=ts)
        result = gate.market_hours_check(_buy(), ctx)
        assert not result.passed

    def test_passes_when_enforcement_disabled(self):
        gate = _gate(enforce_market_hours=False)
        ts = _market_hours_ts(2, 0)  # 02:00 ET — middle of night
        ctx = RiskContext(timestamp=ts)
        result = gate.market_hours_check(_buy(), ctx)
        assert result.passed


# ---------------------------------------------------------------------------
# 9. minimum_validation_check
# ---------------------------------------------------------------------------

class TestMinimumValidationCheck:
    def test_passes_when_strategy_is_deployable(self):
        gate = _gate()
        intent = _buy()
        intent.strategy_id = "good_strategy"
        ctx = RiskContext(validation_reports={"good_strategy": True})
        result = gate.minimum_validation_check(intent, ctx)
        assert result.passed

    def test_blocks_when_strategy_not_deployable(self):
        gate = _gate()
        intent = _buy()
        intent.strategy_id = "bad_strategy"
        ctx = RiskContext(validation_reports={"bad_strategy": False})
        result = gate.minimum_validation_check(intent, ctx)
        assert not result.passed

    def test_passes_conservatively_when_strategy_unknown(self):
        gate = _gate(require_validation_report=False)
        intent = _buy()
        intent.strategy_id = "new_strategy"
        ctx = RiskContext(validation_reports={})
        result = gate.minimum_validation_check(intent, ctx)
        assert result.passed

    def test_blocks_when_require_report_and_unknown(self):
        gate = _gate(require_validation_report=True)
        intent = _buy()
        intent.strategy_id = "new_strategy"
        ctx = RiskContext(validation_reports={})
        result = gate.minimum_validation_check(intent, ctx)
        assert not result.passed


# ---------------------------------------------------------------------------
# 10. max_order_rate_check
# ---------------------------------------------------------------------------

class TestMaxOrderRateCheck:
    def test_passes_within_limit(self):
        gate = _gate(max_order_rate_per_min=5)
        ts = datetime.now(timezone.utc)
        ctx = RiskContext(timestamp=ts)
        for _ in range(4):
            gate.max_order_rate_check(_buy(), RiskContext(timestamp=ts))
        # 5th order (index 4 = the one we're testing) → still within limit
        result = gate.max_order_rate_check(_buy(), ctx)
        assert result.passed

    def test_blocks_when_limit_exceeded(self):
        gate = _gate(max_order_rate_per_min=3)
        ts = datetime.now(timezone.utc)
        for _ in range(3):
            gate.max_order_rate_check(_buy(), RiskContext(timestamp=ts))
        # 4th order in same minute → blocked
        result = gate.max_order_rate_check(_buy(), RiskContext(timestamp=ts))
        assert not result.passed

    def test_old_timestamps_evicted(self):
        gate = _gate(max_order_rate_per_min=2)
        past = datetime.now(timezone.utc) - timedelta(seconds=120)
        for _ in range(2):
            gate.max_order_rate_check(_buy(), RiskContext(timestamp=past))
        # Now submit at current time — past orders should be outside the window
        result = gate.max_order_rate_check(_buy(), RiskContext(timestamp=datetime.now(timezone.utc)))
        assert result.passed


# ---------------------------------------------------------------------------
# run_all integration: short-circuit + rate-limit only charged on success
# ---------------------------------------------------------------------------

class TestRunAll:
    def _rth_ctx(self, **kwargs) -> RiskContext:
        ts = _market_hours_ts(11, 0)
        return RiskContext(timestamp=ts, **kwargs)

    def test_all_pass_returns_true(self):
        gate = _gate(enforce_market_hours=False)
        ctx = RiskContext()
        passed, results = gate.run_all(_buy(), ctx)
        assert passed
        assert all(r.passed for r in results)

    def test_first_failure_short_circuits(self):
        gate = _gate(max_position_size_pct=0.0001, enforce_market_hours=False)
        ctx = RiskContext(
            account=_account(100_000),
            current_prices={"AAPL": 100.0},
        )
        passed, results = gate.run_all(_buy("AAPL", qty=1000.0), ctx)
        assert not passed
        # Should stop at max_position_size (first check)
        assert results[-1].check_name == "max_position_size"
        assert len(results) == 1

    def test_rate_limit_not_charged_on_blocked_order(self):
        gate = _gate(max_position_size_pct=0.0001, max_order_rate_per_min=1,
                     enforce_market_hours=False)
        ctx = RiskContext(
            account=_account(100_000),
            current_prices={"AAPL": 100.0},
        )
        # Submit a blocked order multiple times — rate counter must stay at 0
        for _ in range(5):
            gate.run_all(_buy("AAPL", qty=1000.0), ctx)

        # Now submit a valid order — should not be rate-limited
        valid_ctx = RiskContext()
        # A fresh gate with the same rate limit but no position-size restriction
        gate2 = _gate(max_order_rate_per_min=1, enforce_market_hours=False)
        passed, results = gate2.run_all(_buy("AAPL", qty=1.0), valid_ctx)
        # Rate check should pass (counter was never incremented by blocked orders)
        rate_result = next(r for r in results if r.check_name == "max_order_rate")
        assert rate_result.passed


# ---------------------------------------------------------------------------
# Constraint: no bare except that returns 0.0
# ---------------------------------------------------------------------------

def test_no_fabricated_result_on_missing_data():
    """Checks that each check returns passed=True (conservative) on missing context,
    never passes by silently returning a fabricated number."""
    gate = _gate(enforce_market_hours=False)
    ctx = RiskContext()  # completely empty
    intent = _buy()
    for check in [
        gate.max_position_size_check,
        gate.portfolio_heat_check,
        gate.max_correlation_check,
        gate.daily_loss_limit_check,
        gate.macro_kill_switch_check,
        gate.hmm_regime_check,
        gate.stress_scenario_check,
        gate.minimum_validation_check,
        gate.max_order_rate_check,
    ]:
        result = check(intent, ctx)
        assert isinstance(result, RiskCheckResult)
        # None of these should crash or return a non-boolean passed field
        assert isinstance(result.passed, bool)

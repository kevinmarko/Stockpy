"""
execution/risk_gate.py
======================
Pre-trade risk gate: a pipeline of synchronous checks every OrderIntent must
pass before submission to the broker.

Design
------
* All checks are **synchronous** — callers pre-fetch broker snapshots
  (account, open_positions) asynchronously and inject them via
  ``RiskContext``.  This keeps the gate fast and easily unit-testable
  without async mocking.
* Unknown or missing context (``None`` fields) causes the associated check
  to **pass conservatively** (never block due to missing data).  The single
  exception is ``macro_kill_switch_check``, which reads the fully-computed
  ``MacroEconomicDTO.killSwitch`` property — a ``None`` macro skips that
  check (pass).
* ``max_order_rate_check`` is run **last** so that the rate-limit counter is
  only incremented when all other checks have already passed.  Blocked orders
  do not consume rate-limit budget.
* Correlation is checked via absolute value so both over-correlated longs AND
  negatively-correlated shorts (which hedge the book and are allowable) are
  handled correctly: negative correlation ``r < -MAX_CORRELATION`` is also
  blocked because such a position would amplify tail P&L swings.

Checks (in execution order)
---------------------------
1.  max_position_size_check  — notional > MAX_POSITION_WEIGHT * equity
2.  portfolio_heat_check     — open drawdown risk > MAX_PORTFOLIO_HEAT (6%)
3.  max_correlation_check    — |r| > MAX_CORRELATION (0.85) with any holding
4.  daily_loss_limit_check   — intraday P&L < -DAILY_LOSS_LIMIT_PCT (2%)
5.  macro_kill_switch_check  — MacroEconomicDTO.killSwitch is True
6.  hmm_regime_check         — HMM risk-off probability > 0.80
7.  stress_scenario_check    — VIX > 30 AND premium-selling strategy
8.  market_hours_check       — outside NYSE RTH 09:30–16:00 ET
9.  minimum_validation_check — strategy has deployable=False in registry
10. max_order_rate_check     — > MAX_ORDER_RATE_PER_MIN orders in 60 s
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from execution.broker_base import AccountSnapshot, OrderIntent, OrderSide, PositionSnapshot
from settings import settings

if TYPE_CHECKING:
    # MacroEconomicDTO is imported only for type checking to avoid a circular
    # dependency at runtime (dto_models -> ... -> execution -> dto_models).
    from dto_models import MacroEconomicDTO

logger = logging.getLogger(__name__)

# NYSE regular trading hours (Eastern Time)
_ET = ZoneInfo("America/New_York")
_RTH_OPEN_H, _RTH_OPEN_M = 9, 30    # 09:30 ET
_RTH_CLOSE_H, _RTH_CLOSE_M = 16, 0  # 16:00 ET


@dataclass
class RiskCheckResult:
    """Result from a single pre-trade risk check."""
    check_name: str
    passed: bool
    reason: str


@dataclass
class RiskContext:
    """
    Per-call context supplied to ``PreTradeRiskGate.run_all()``.

    All fields are optional.  A ``None`` field causes the associated check
    to **pass conservatively** — checks never block due to missing data.

    Parameters
    ----------
    macro : MacroEconomicDTO | None
        Current macroeconomic state (for killSwitch + HMM checks).
    open_positions : list[PositionSnapshot]
        Broker's current open-position snapshot (for heat + correlation).
    account : AccountSnapshot | None
        Current account equity / cash (for position-size + heat checks).
    returns_df : pd.DataFrame | None
        Daily-return history; columns = ticker symbols, rows = dates.
        Used by ``max_correlation_check``.
    start_of_day_equity : float | None
        Account equity at market open (for daily loss-limit check).
    validation_reports : dict[str, bool]
        Maps ``strategy_id`` → ``deployable`` flag.  Missing key = pass.
    is_premium_sell_strategy : bool
        When True, ``stress_scenario_check`` applies (VIX > 30 → block).
    current_prices : dict[str, float]
        Symbol → last price, used by ``max_position_size_check``.
    timestamp : datetime | None
        Override the wall-clock time (for deterministic tests of
        ``market_hours_check``).  Must be UTC-aware or naïve-UTC.
    """
    macro: Optional["MacroEconomicDTO"] = None
    open_positions: list[PositionSnapshot] = field(default_factory=list)
    account: Optional[AccountSnapshot] = None
    returns_df: Optional[pd.DataFrame] = None
    start_of_day_equity: Optional[float] = None
    validation_reports: dict[str, bool] = field(default_factory=dict)
    is_premium_sell_strategy: bool = False
    current_prices: dict[str, float] = field(default_factory=dict)
    timestamp: Optional[datetime] = None


class PreTradeRiskGate:
    """
    Ten-check pre-trade risk pipeline.

    Parameters
    ----------
    All threshold kwargs default to the ``settings.*`` counterpart so they
    can be overridden per-instance in unit tests without monkey-patching.
    """

    def __init__(
        self,
        *,
        max_position_size_pct: Optional[float] = None,
        max_portfolio_heat: Optional[float] = None,
        max_correlation: Optional[float] = None,
        daily_loss_limit_pct: Optional[float] = None,
        max_order_rate_per_min: Optional[int] = None,
        hmm_risk_off_block_threshold: Optional[float] = None,
        enforce_market_hours: Optional[bool] = None,
        require_validation_report: bool = False,
    ) -> None:
        self.max_position_size_pct = (
            max_position_size_pct
            if max_position_size_pct is not None
            else settings.MAX_POSITION_WEIGHT
        )
        self.max_portfolio_heat = (
            max_portfolio_heat
            if max_portfolio_heat is not None
            else settings.MAX_PORTFOLIO_HEAT
        )
        self.max_correlation = (
            max_correlation
            if max_correlation is not None
            else settings.MAX_CORRELATION
        )
        self.daily_loss_limit_pct = (
            daily_loss_limit_pct
            if daily_loss_limit_pct is not None
            else settings.DAILY_LOSS_LIMIT_PCT
        )
        self.max_order_rate_per_min = (
            max_order_rate_per_min
            if max_order_rate_per_min is not None
            else settings.MAX_ORDER_RATE_PER_MIN
        )
        self.hmm_risk_off_block_threshold = (
            hmm_risk_off_block_threshold
            if hmm_risk_off_block_threshold is not None
            else settings.HMM_RISK_OFF_BLOCK_THRESHOLD
        )
        self.enforce_market_hours = (
            enforce_market_hours
            if enforce_market_hours is not None
            else settings.RISK_GATE_ENFORCE_MARKET_HOURS
        )
        # When True, block strategies that have no validation report.
        # False (default): pass conservatively if strategy_id is unknown.
        self.require_validation_report = require_validation_report

        # Rolling deque of UTC timestamps for order-rate tracking.
        # Only orders that pass ALL prior checks increment this counter.
        self._order_timestamps: deque[datetime] = deque()

    # ------------------------------------------------------------------
    # Individual checks (alphabetical within functional groups)
    # ------------------------------------------------------------------

    def max_position_size_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block BUY when notional > max_position_size_pct * account equity."""
        name = "max_position_size"
        if context.account is None or context.account.equity <= 0:
            return RiskCheckResult(name, True, "no account snapshot — skipping")

        price = context.current_prices.get(intent.symbol.upper())
        if price is None or price <= 0:
            return RiskCheckResult(name, True, "no market price available — skipping")

        notional = intent.qty * price
        max_notional = self.max_position_size_pct * context.account.equity
        if notional > max_notional:
            return RiskCheckResult(
                name,
                False,
                f"{intent.symbol} notional ${notional:,.0f} > max ${max_notional:,.0f} "
                f"({self.max_position_size_pct*100:.0f}% of ${context.account.equity:,.0f} equity)",
            )
        return RiskCheckResult(
            name, True, f"notional ${notional:,.0f} ≤ limit ${max_notional:,.0f}"
        )

    def portfolio_heat_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block new BUY when aggregate open drawdown > MAX_PORTFOLIO_HEAT."""
        name = "portfolio_heat"
        if intent.side != OrderSide.BUY:
            return RiskCheckResult(name, True, "SELL order — heat check skipped")

        if not context.open_positions:
            return RiskCheckResult(name, True, "no open positions — heat = 0%")

        if context.account is None or context.account.equity <= 0:
            return RiskCheckResult(name, True, "no account snapshot — skipping")

        # Open risk = sum of unrealized losses (adverse P&L only)
        total_risk = sum(
            abs(p.unrealized_pl)
            for p in context.open_positions
            if p.unrealized_pl < 0
        )
        heat = total_risk / context.account.equity
        if heat > self.max_portfolio_heat:
            return RiskCheckResult(
                name,
                False,
                f"portfolio heat {heat*100:.2f}% > limit {self.max_portfolio_heat*100:.2f}% — "
                "halting new long exposure",
            )
        return RiskCheckResult(
            name, True, f"heat {heat*100:.2f}% ≤ {self.max_portfolio_heat*100:.2f}%"
        )

    def max_correlation_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block when |r| > MAX_CORRELATION between new symbol and any existing position."""
        name = "max_correlation"
        if context.returns_df is None or context.returns_df.empty:
            return RiskCheckResult(name, True, "no returns data — skipping correlation check")

        new_sym = intent.symbol.upper()
        existing_syms = [p.symbol.upper() for p in context.open_positions]
        if not existing_syms:
            return RiskCheckResult(name, True, "no existing positions — check not applicable")

        if new_sym not in context.returns_df.columns:
            return RiskCheckResult(name, True, f"{new_sym} not in returns_df — skipping")

        new_ret = context.returns_df[new_sym].dropna()
        if len(new_ret) < 20:
            return RiskCheckResult(name, True, f"{new_sym} has < 20 observations — skipping")

        for sym in existing_syms:
            if sym not in context.returns_df.columns:
                continue
            existing_ret = context.returns_df[sym].dropna()
            common = new_ret.index.intersection(existing_ret.index)
            if len(common) < 20:
                continue
            corr = float(new_ret.loc[common].corr(existing_ret.loc[common]))
            if abs(corr) > self.max_correlation:
                return RiskCheckResult(
                    name,
                    False,
                    f"{new_sym} ↔ {sym}: |r|={abs(corr):.3f} > threshold {self.max_correlation:.2f}",
                )
        return RiskCheckResult(
            name, True, f"all pairwise |r| ≤ {self.max_correlation:.2f}"
        )

    def daily_loss_limit_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block BUY when today's P&L < -DAILY_LOSS_LIMIT_PCT of start-of-day equity."""
        name = "daily_loss_limit"
        if intent.side != OrderSide.BUY:
            return RiskCheckResult(name, True, "SELL order — loss limit skipped")

        if context.start_of_day_equity is None or context.account is None:
            return RiskCheckResult(name, True, "start-of-day equity unavailable — skipping")

        if context.start_of_day_equity <= 0:
            return RiskCheckResult(name, True, "start-of-day equity ≤ 0 — skipping")

        pnl_pct = (context.account.equity - context.start_of_day_equity) / context.start_of_day_equity
        if pnl_pct < -self.daily_loss_limit_pct:
            return RiskCheckResult(
                name,
                False,
                f"intraday P&L {pnl_pct*100:.2f}% < limit -{self.daily_loss_limit_pct*100:.2f}% — "
                "halting new orders for today",
            )
        return RiskCheckResult(
            name, True, f"intraday P&L {pnl_pct*100:.2f}% ≥ -{self.daily_loss_limit_pct*100:.2f}%"
        )

    def macro_kill_switch_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block all new BUY orders when MacroEconomicDTO.killSwitch is True."""
        name = "macro_kill_switch"
        if context.macro is None:
            return RiskCheckResult(name, True, "no macro context — skipping")

        if intent.side != OrderSide.BUY:
            return RiskCheckResult(name, True, "SELL order — macro kill switch skipped")

        if context.macro.killSwitch:
            return RiskCheckResult(
                name,
                False,
                f"macro kill switch active (regime={context.macro.market_regime}, "
                f"vix={context.macro.vix:.1f}, sahm={context.macro.sahm_rule_indicator:.2f})",
            )
        return RiskCheckResult(
            name, True, f"macro kill switch inactive (regime={context.macro.market_regime})"
        )

    def hmm_regime_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block new longs when HMM risk-off probability > HMM_RISK_OFF_BLOCK_THRESHOLD."""
        name = "hmm_regime"
        if context.macro is None or context.macro.hmm_risk_on_probability is None:
            return RiskCheckResult(name, True, "HMM probability unavailable — skipping")

        if intent.side != OrderSide.BUY:
            return RiskCheckResult(name, True, "SELL order — HMM check skipped")

        risk_off_prob = 1.0 - context.macro.hmm_risk_on_probability
        if risk_off_prob > self.hmm_risk_off_block_threshold:
            return RiskCheckResult(
                name,
                False,
                f"HMM risk-off={risk_off_prob:.3f} > block threshold "
                f"{self.hmm_risk_off_block_threshold:.2f} — blocking new longs",
            )
        return RiskCheckResult(
            name, True, f"HMM risk-off={risk_off_prob:.3f} ≤ {self.hmm_risk_off_block_threshold:.2f}"
        )

    def stress_scenario_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block premium-selling orders during a stress / elevated-vol regime (VIX > 30)."""
        name = "stress_scenario"
        if not context.is_premium_sell_strategy:
            return RiskCheckResult(name, True, "not a premium-sell strategy — skipped")

        if context.macro is None:
            return RiskCheckResult(name, True, "no macro context — skipping")

        if context.macro.vix > 30.0:
            return RiskCheckResult(
                name,
                False,
                f"stress scenario: VIX={context.macro.vix:.1f} > 30 — "
                "blocking premium-sell orders",
            )
        return RiskCheckResult(
            name, True, f"VIX={context.macro.vix:.1f} ≤ 30 — stress check passed"
        )

    def market_hours_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block orders outside NYSE regular trading hours unless enforcement is disabled."""
        name = "market_hours"
        if not self.enforce_market_hours:
            return RiskCheckResult(name, True, "market-hours enforcement disabled")

        now_utc = context.timestamp or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        now_et = now_utc.astimezone(_ET)

        open_time = now_et.replace(
            hour=_RTH_OPEN_H, minute=_RTH_OPEN_M, second=0, microsecond=0
        )
        close_time = now_et.replace(
            hour=_RTH_CLOSE_H, minute=_RTH_CLOSE_M, second=0, microsecond=0
        )

        if not (open_time <= now_et <= close_time):
            return RiskCheckResult(
                name,
                False,
                f"market closed: {now_et.strftime('%H:%M')} ET is outside RTH "
                f"(09:30–16:00 ET)",
            )
        return RiskCheckResult(
            name, True, f"market open: {now_et.strftime('%H:%M')} ET within RTH"
        )

    def minimum_validation_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block orders from strategies whose validation report marks deployable=False."""
        name = "minimum_validation"
        strategy_id = intent.strategy_id

        if strategy_id not in context.validation_reports:
            if self.require_validation_report:
                return RiskCheckResult(
                    name,
                    False,
                    f"strategy '{strategy_id}' has no validation report — "
                    "blocked (require_validation_report=True)",
                )
            return RiskCheckResult(
                name, True,
                f"strategy '{strategy_id}' not in validation registry — passing conservatively",
            )

        deployable = context.validation_reports[strategy_id]
        if not deployable:
            return RiskCheckResult(
                name,
                False,
                f"strategy '{strategy_id}' has deployable=False — order blocked",
            )
        return RiskCheckResult(name, True, f"strategy '{strategy_id}' is deployable")

    def max_order_rate_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block when ≥ MAX_ORDER_RATE_PER_MIN orders have been submitted in the last 60 s.

        This check is run **last** so the rate-limit counter is only incremented
        for orders that passed all prior checks.  Blocked orders do not burn
        rate-limit budget.
        """
        name = "max_order_rate"
        now = context.timestamp or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        window_start = now - timedelta(seconds=60)

        # Evict timestamps older than the rolling window
        while self._order_timestamps and self._order_timestamps[0] < window_start:
            self._order_timestamps.popleft()

        count = len(self._order_timestamps)
        if count >= self.max_order_rate_per_min:
            return RiskCheckResult(
                name,
                False,
                f"{count} orders in past 60 s ≥ limit {self.max_order_rate_per_min} — "
                "rate limit exceeded",
            )
        # Record only after all prior checks passed (gate evaluates checks in order,
        # stops at first failure, so if we reach here all prior checks passed).
        self._order_timestamps.append(now)
        return RiskCheckResult(
            name, True, f"{count + 1} orders in past 60 s (limit {self.max_order_rate_per_min})"
        )

    # ------------------------------------------------------------------
    # Aggregate runner
    # ------------------------------------------------------------------

    def run_all(
        self, intent: OrderIntent, context: RiskContext
    ) -> tuple[bool, list[RiskCheckResult]]:
        """
        Run all checks in sequence; short-circuit at the first failure.

        Returns
        -------
        (all_passed, results)
            ``all_passed`` is True only when every check returned passed=True.
            ``results`` contains every check that was evaluated (up to and
            including the first failure).
        """
        checks = [
            self.max_position_size_check,
            self.portfolio_heat_check,
            self.max_correlation_check,
            self.daily_loss_limit_check,
            self.macro_kill_switch_check,
            self.hmm_regime_check,
            self.stress_scenario_check,
            self.market_hours_check,
            self.minimum_validation_check,
            # Rate-limit check last — only charges budget when all other checks pass.
            self.max_order_rate_check,
        ]
        results: list[RiskCheckResult] = []
        for check_fn in checks:
            result = check_fn(intent, context)
            results.append(result)
            if not result.passed:
                logger.warning(
                    "PRE-TRADE GATE BLOCKED [%s]: %s | %s %s x %.4f",
                    result.check_name,
                    result.reason,
                    intent.side.value.upper(),
                    intent.symbol,
                    intent.qty,
                )
                return False, results

        logger.debug(
            "Pre-trade gate PASSED for %s %s x %.4f (%d checks)",
            intent.side.value.upper(),
            intent.symbol,
            intent.qty,
            len(results),
        )
        return True, results

"""
execution/risk_gate.py
======================
Synchronous pre-trade risk-check pipeline.  Every ``OrderIntent`` must pass
``PreTradeRiskGate.run_all()`` before it reaches the broker.

Design
------
* All checks are **synchronous** — callers pre-fetch broker snapshots async
  and inject them via ``RiskContext``.  This keeps the gate fast and
  unit-testable without async mocking.
* Unknown / missing context fields (``None``) cause the associated check to
  **pass conservatively** — a check never blocks due to absent data.
* ``max_order_rate_check`` is always run **last** so the rate counter is only
  incremented for orders that cleared all other checks.  Blocked orders never
  consume rate-limit budget.
* Correlation is checked via **absolute value** — both highly-positive (herding
  risk) and highly-negative (tail P&L amplification) positions are blocked.

Checks (execution order)
------------------------
1.  max_position_size  — notional > MAX_POSITION_WEIGHT * account equity
2.  portfolio_heat     — adverse open drawdown > MAX_PORTFOLIO_HEAT (6%)
3.  max_correlation    — |r| > MAX_CORRELATION (0.85) with any existing holding
4.  daily_loss_limit   — intraday P&L < -DAILY_LOSS_LIMIT_PCT (2%)
5.  macro_kill_switch  — MacroEconomicDTO.killSwitch is True
6.  hmm_regime         — HMM risk-off prob > HMM_RISK_OFF_BLOCK_THRESHOLD (0.80)
7.  stress_scenario    — VIX > 30 AND premium-selling strategy
8.  market_hours       — outside NYSE RTH 09:30–16:00 ET
9.  minimum_validation — strategy has deployable=False in registry
10. max_order_rate      — > MAX_ORDER_RATE_PER_MIN orders in 60 s
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from execution.broker_base import AccountSnapshot, OrderIntent, OrderSide, PositionSnapshot
from settings import settings

if TYPE_CHECKING:
    # Import only for type annotations to avoid circular dependency at runtime.
    from dto_models import MacroEconomicDTO

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_RTH_OPEN = (9, 30)    # 09:30 ET
_RTH_CLOSE = (16, 0)   # 16:00 ET


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
    to pass conservatively — never blocks due to missing data.

    Parameters
    ----------
    macro : MacroEconomicDTO | None
        Current macro state (macro_kill_switch + hmm_regime + stress checks).
    open_positions : list[PositionSnapshot]
        Broker's current open-position snapshot (heat + correlation checks).
    account : AccountSnapshot | None
        Current account equity / cash (position-size + heat + loss-limit checks).
    returns_df : pd.DataFrame | None
        Daily-return history; columns = ticker symbols.  Used by correlation check.
    start_of_day_equity : float | None
        Account equity at market open (daily loss-limit check).
    validation_reports : dict[str, bool]
        Maps strategy_id → deployable flag.  Missing key = pass.
    is_premium_sell_strategy : bool
        When True, stress_scenario_check (VIX > 30) applies.
    current_prices : dict[str, float]
        Symbol → last price for position-size check.
    timestamp : datetime | None
        Override wall-clock time (UTC or naïve-UTC) for deterministic tests of
        market_hours_check and max_order_rate_check.
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

    Thresholds default to ``settings.*`` counterparts and can be overridden
    per-instance for unit tests without monkey-patching global settings.
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
            max_portfolio_heat if max_portfolio_heat is not None else settings.MAX_PORTFOLIO_HEAT
        )
        self.max_correlation = (
            max_correlation if max_correlation is not None else settings.MAX_CORRELATION
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
        # When True, block a strategy that has no entry in validation_reports.
        # Default False: unknown strategy passes conservatively.
        self.require_validation_report = require_validation_report

        # Rolling deque of UTC timestamps for rate-limit tracking.
        # Only populated when ALL prior checks pass — blocked orders never burn budget.
        self._order_timestamps: deque[datetime] = deque()

    # ------------------------------------------------------------------
    # Individual checks
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
            return RiskCheckResult(name, True, "no market price — skipping")
        notional = intent.qty * price
        max_notional = self.max_position_size_pct * context.account.equity
        if notional > max_notional:
            return RiskCheckResult(
                name, False,
                f"{intent.symbol} notional ${notional:,.0f} > max ${max_notional:,.0f} "
                f"({self.max_position_size_pct*100:.0f}% of ${context.account.equity:,.0f} equity)",
            )
        return RiskCheckResult(name, True, f"notional ${notional:,.0f} ≤ ${max_notional:,.0f}")

    def portfolio_heat_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block new BUY when aggregate adverse open P&L > MAX_PORTFOLIO_HEAT."""
        name = "portfolio_heat"
        if intent.side != OrderSide.BUY:
            return RiskCheckResult(name, True, "SELL — heat check skipped")
        if not context.open_positions:
            return RiskCheckResult(name, True, "no open positions — heat = 0%")
        if context.account is None or context.account.equity <= 0:
            return RiskCheckResult(name, True, "no account snapshot — skipping")
        adverse_pl = sum(
            abs(p.unrealized_pl) for p in context.open_positions if p.unrealized_pl < 0
        )
        heat = adverse_pl / context.account.equity
        if heat > self.max_portfolio_heat:
            return RiskCheckResult(
                name, False,
                f"portfolio heat {heat*100:.2f}% > limit {self.max_portfolio_heat*100:.2f}% — "
                "halting new long exposure",
            )
        return RiskCheckResult(name, True, f"heat {heat*100:.2f}% ≤ {self.max_portfolio_heat*100:.2f}%")

    def max_correlation_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block when |r| > MAX_CORRELATION between new symbol and any existing position."""
        name = "max_correlation"
        if context.returns_df is None or context.returns_df.empty:
            return RiskCheckResult(name, True, "no returns data — skipping")
        new_sym = intent.symbol.upper()
        existing = [p.symbol.upper() for p in context.open_positions]
        if not existing:
            return RiskCheckResult(name, True, "no existing positions — not applicable")
        if new_sym not in context.returns_df.columns:
            return RiskCheckResult(name, True, f"{new_sym} not in returns_df — skipping")
        new_ret = context.returns_df[new_sym].dropna()
        if len(new_ret) < 20:
            return RiskCheckResult(name, True, f"{new_sym} has < 20 observations — skipping")
        for sym in existing:
            if sym not in context.returns_df.columns:
                continue
            other_ret = context.returns_df[sym].dropna()
            common = new_ret.index.intersection(other_ret.index)
            if len(common) < 20:
                continue
            corr = float(new_ret.loc[common].corr(other_ret.loc[common]))
            if abs(corr) > self.max_correlation:
                return RiskCheckResult(
                    name, False,
                    f"{new_sym} ↔ {sym}: |r|={abs(corr):.3f} > threshold {self.max_correlation:.2f}",
                )
        return RiskCheckResult(name, True, f"all pairwise |r| ≤ {self.max_correlation:.2f}")

    def daily_loss_limit_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block BUY when today's P&L < -DAILY_LOSS_LIMIT_PCT of start-of-day equity."""
        name = "daily_loss_limit"
        if intent.side != OrderSide.BUY:
            return RiskCheckResult(name, True, "SELL — loss limit skipped")
        if context.start_of_day_equity is None or context.account is None:
            return RiskCheckResult(name, True, "start-of-day equity unavailable — skipping")
        if context.start_of_day_equity <= 0:
            return RiskCheckResult(name, True, "start-of-day equity ≤ 0 — skipping")
        pnl_pct = (context.account.equity - context.start_of_day_equity) / context.start_of_day_equity
        if pnl_pct < -self.daily_loss_limit_pct:
            return RiskCheckResult(
                name, False,
                f"intraday P&L {pnl_pct*100:.2f}% < limit -{self.daily_loss_limit_pct*100:.2f}% — "
                "halting new orders",
            )
        return RiskCheckResult(
            name, True, f"intraday P&L {pnl_pct*100:.2f}% ≥ -{self.daily_loss_limit_pct*100:.2f}%"
        )

    def macro_kill_switch_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block all new BUY orders when MacroEconomicDTO.killSwitch is True.

        The check is skipped entirely when ``settings.MACRO_REGIME_GATE_ENABLED``
        is ``False`` — the operator has selected hybrid mode (technical signals
        run without macro veto).  The GUI Observability tab controls this flag
        and displays a persistent warning when it is off.
        """
        name = "macro_kill_switch"
        # Operator override: gate disabled → always pass (hybrid mode).
        if not settings.MACRO_REGIME_GATE_ENABLED:
            return RiskCheckResult(
                name, True,
                "macro regime gate disabled by operator (MACRO_REGIME_GATE_ENABLED=false)"
            )
        if context.macro is None:
            return RiskCheckResult(name, True, "no macro context — skipping")
        if intent.side != OrderSide.BUY:
            return RiskCheckResult(name, True, "SELL — macro kill switch skipped")
        if context.macro.killSwitch:
            sahm = getattr(context.macro, "sahm_rule_indicator", None)
            sahm_str = f", sahm={sahm:.2f}" if isinstance(sahm, (int, float)) else ""
            return RiskCheckResult(
                name, False,
                f"macro kill switch active (regime={context.macro.market_regime}, "
                f"vix={context.macro.vix:.1f}{sahm_str})",
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
            return RiskCheckResult(name, True, "SELL — HMM check skipped")
        risk_off = 1.0 - context.macro.hmm_risk_on_probability
        if risk_off > self.hmm_risk_off_block_threshold:
            return RiskCheckResult(
                name, False,
                f"HMM risk-off={risk_off:.3f} > block threshold {self.hmm_risk_off_block_threshold:.2f}",
            )
        return RiskCheckResult(
            name, True, f"HMM risk-off={risk_off:.3f} ≤ {self.hmm_risk_off_block_threshold:.2f}"
        )

    def stress_scenario_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block premium-selling orders during elevated-vol regimes (VIX > 30)."""
        name = "stress_scenario"
        if not context.is_premium_sell_strategy:
            return RiskCheckResult(name, True, "not a premium-sell strategy — skipped")
        if context.macro is None:
            return RiskCheckResult(name, True, "no macro context — skipping")
        if context.macro.vix > 30.0:
            return RiskCheckResult(
                name, False,
                f"VIX={context.macro.vix:.1f} > 30 — blocking premium-sell orders",
            )
        return RiskCheckResult(name, True, f"VIX={context.macro.vix:.1f} ≤ 30")

    def market_hours_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block orders outside NYSE regular trading hours (09:30–16:00 ET)."""
        name = "market_hours"
        if not self.enforce_market_hours:
            return RiskCheckResult(name, True, "market-hours enforcement disabled")
        now_utc = context.timestamp or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        now_et = now_utc.astimezone(_ET)
        open_t = now_et.replace(hour=_RTH_OPEN[0], minute=_RTH_OPEN[1], second=0, microsecond=0)
        close_t = now_et.replace(hour=_RTH_CLOSE[0], minute=_RTH_CLOSE[1], second=0, microsecond=0)
        if not (open_t <= now_et <= close_t):
            return RiskCheckResult(
                name, False,
                f"market closed: {now_et.strftime('%H:%M')} ET is outside RTH (09:30–16:00 ET)",
            )
        return RiskCheckResult(name, True, f"market open: {now_et.strftime('%H:%M')} ET")

    def minimum_validation_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block orders from strategies whose validation report marks deployable=False."""
        name = "minimum_validation"
        sid = intent.strategy_id
        if sid not in context.validation_reports:
            if self.require_validation_report:
                return RiskCheckResult(
                    name, False,
                    f"strategy '{sid}' has no validation report — blocked (require_validation_report=True)",
                )
            return RiskCheckResult(name, True, f"strategy '{sid}' not in registry — pass conservatively")
        if not context.validation_reports[sid]:
            return RiskCheckResult(name, False, f"strategy '{sid}' is deployable=False — blocked")
        return RiskCheckResult(name, True, f"strategy '{sid}' is deployable")

    def max_order_rate_check(
        self, intent: OrderIntent, context: RiskContext
    ) -> RiskCheckResult:
        """Block when ≥ MAX_ORDER_RATE_PER_MIN orders submitted in the last 60 s.

        MUST be the last check — timestamp is only recorded after all prior checks
        pass.  Blocked orders do not consume rate-limit budget.
        """
        name = "max_order_rate"
        now = context.timestamp or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        cutoff = now - timedelta(seconds=60)
        while self._order_timestamps and self._order_timestamps[0] < cutoff:
            self._order_timestamps.popleft()
        count = len(self._order_timestamps)
        if count >= self.max_order_rate_per_min:
            return RiskCheckResult(
                name, False,
                f"{count} orders in past 60 s ≥ limit {self.max_order_rate_per_min}",
            )
        self._order_timestamps.append(now)
        return RiskCheckResult(name, True, f"{count + 1}/{self.max_order_rate_per_min} in past 60 s")

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
            ``results`` contains checks evaluated up to and including the first failure.
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
            self.max_order_rate_check,  # always last
        ]
        results: list[RiskCheckResult] = []
        for fn in checks:
            result = fn(intent, context)
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
                self._append_block_log(result, intent, context)
                return False, results
        logger.debug(
            "Pre-trade gate PASSED for %s %s x %.4f (%d checks)",
            intent.side.value.upper(), intent.symbol, intent.qty, len(results),
        )
        return True, results

    def _append_block_log(
        self,
        result: RiskCheckResult,
        intent: OrderIntent,
        context: RiskContext,
    ) -> None:
        """Append a blocked-order entry to OUTPUT_DIR/risk_gate_blocks.jsonl.

        The file is capped at 1 000 lines by periodic truncation (not per-write,
        for performance).  The dashboard reads the tail of this file for display.
        Errors are swallowed so a logging failure never impacts order flow.
        """
        try:
            log_path: Path = settings.OUTPUT_DIR / "risk_gate_blocks.jsonl"
            entry = {
                "ts": (context.timestamp or datetime.now(timezone.utc)).isoformat(),
                "check": result.check_name,
                "reason": result.reason,
                "symbol": intent.symbol,
                "side": intent.side.value,
                "qty": intent.qty,
                "strategy_id": intent.strategy_id,
            }
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.debug("risk_gate block log write failed (non-fatal): %s", exc)

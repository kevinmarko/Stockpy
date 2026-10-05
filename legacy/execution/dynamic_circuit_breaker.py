"""
execution/dynamic_circuit_breaker.py
====================================
Intraday Dynamic Circuit Breakers & Flash Liquidity Guardrails.

Provides real-time quantitative monitoring and protection against:
1. Volatility Jumps: Computes 5m EWMA realized vol vs 20d baseline. If Z-score > 3.5 => SOFT_HALT (VOLATILITY_BURST_HALT).
2. Order Flow Imbalance (OFI) & Toxicity Crash Shield: Computes OFI = Δq_b - Δq_a. When OFI < -threshold AND VPIN > 0.40 => SOFT_HALT (FLASH_CRASH_SHIELD).
3. Intraday Loss Velocity Brake: When d(PnL)/dt <= -(Daily Loss Limit / 30 mins) => HARD_HALT (LOSS_VELOCITY_BREACH).

Circuit Breaker States:
- NORMAL: Standard trading conditions. Full order flow authorized.
- CAUTION: Elevated volatility or mild toxicity. Full order flow authorized with audit logging.
- SOFT_HALT: Asymmetric shield. Blocks new risk-increasing BUY orders while permitting risk-reducing SELL / TRIM / exit orders.
- HARD_HALT: Critical breach. Blocks all order submissions across the entire system.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from execution.broker_base import OrderIntent, OrderSide
from execution.kill_switch import GlobalKillSwitch
from settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State and Metrics Types
# ---------------------------------------------------------------------------

class CircuitBreakerState(str, Enum):
    """Lifecycle states for the dynamic circuit breaker."""
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    SOFT_HALT = "SOFT_HALT"
    HARD_HALT = "HARD_HALT"


@dataclass
class CircuitBreakerMetrics:
    """Quantitative snapshot and operational state of the dynamic circuit breaker."""
    state: CircuitBreakerState = CircuitBreakerState.NORMAL
    volatility_zscore: Optional[float] = None
    vpin: Optional[float] = None
    ofi: Optional[float] = None
    loss_velocity_per_min: Optional[float] = None
    reason: Optional[str] = None
    updated_at: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to a JSON-serializable dictionary."""
        return {
            "state": self.state.value if isinstance(self.state, CircuitBreakerState) else str(self.state),
            "volatility_zscore": self.volatility_zscore,
            "vpin": self.vpin,
            "ofi": self.ofi,
            "loss_velocity_per_min": self.loss_velocity_per_min,
            "reason": self.reason,
            "updated_at": self.updated_at,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CircuitBreakerMetrics:
        """Construct CircuitBreakerMetrics from a dictionary."""
        state_raw = data.get("state", CircuitBreakerState.NORMAL.value)
        try:
            state = CircuitBreakerState(state_raw)
        except ValueError:
            state = CircuitBreakerState.NORMAL

        return cls(
            state=state,
            volatility_zscore=data.get("volatility_zscore"),
            vpin=data.get("vpin"),
            ofi=data.get("ofi"),
            loss_velocity_per_min=data.get("loss_velocity_per_min"),
            reason=data.get("reason"),
            updated_at=data.get("updated_at"),
            extra=data.get("extra", {}),
        )


# ---------------------------------------------------------------------------
# Quantitative Core Calculations (Pure stdlib + numpy + pandas)
# ---------------------------------------------------------------------------

def calculate_volatility_zscore_from_vols(
    realized_vol_5m: float,
    baseline_20d_vol: float,
    baseline_vol_std: Optional[float] = None,
) -> float:
    """
    Computes volatility Z-score from direct volatility values:
    Z = (realized_vol_5m - baseline_20d_vol) / baseline_vol_std.
    """
    if baseline_vol_std is not None and baseline_vol_std > 0:
        vol_std = baseline_vol_std
    else:
        # Conservative baseline standard deviation: 15% of baseline vol
        vol_std = max(0.15 * baseline_20d_vol, 1e-4)

    return float((realized_vol_5m - baseline_20d_vol) / vol_std)


def compute_volatility_zscore(
    intraday_returns_or_prices: Union[Sequence[float], pd.Series, np.ndarray],
    baseline_20d_vol: float,
    baseline_vol_std: Optional[float] = None,
    is_prices: bool = False,
    ewma_span: int = 12,
) -> float:
    """
    Computes 5m EWMA realized volatility Z-score relative to a 20d baseline.

    Parameters
    ----------
    intraday_returns_or_prices : Sequence[float] | pd.Series | np.ndarray
        Recent intraday 5m price bars or returns series.
    baseline_20d_vol : float
        20-day baseline realized volatility.
    baseline_vol_std : float | None
        Standard deviation of the 20-day baseline volatility.
    is_prices : bool
        If True, converts prices into pct_change returns first.
    ewma_span : int
        Span for the EWMA rolling window (default 12 for 1 hour of 5m bars).
    """
    if len(intraday_returns_or_prices) == 0:
        return 0.0

    s = pd.Series(intraday_returns_or_prices, dtype=float).dropna()
    if is_prices:
        s = s.pct_change().dropna()

    if len(s) < 2:
        return 0.0

    # Calculate EWMA realized volatility
    ewma_std = s.ewm(span=ewma_span).std()
    current_vol = float(ewma_std.iloc[-1])
    if pd.isna(current_vol):
        return 0.0

    return calculate_volatility_zscore_from_vols(
        realized_vol_5m=current_vol,
        baseline_20d_vol=baseline_20d_vol,
        baseline_vol_std=baseline_vol_std,
    )


def compute_ofi(
    delta_bid_qty: float,
    delta_ask_qty: float,
) -> float:
    """
    Computes Order Flow Imbalance: OFI = Δq_b - Δq_a.
    Negative values indicate net selling / ask accumulation / bid depletion.
    """
    return float(delta_bid_qty - delta_ask_qty)


def compute_ofi_from_quotes(
    bids: Sequence[Tuple[float, float]],
    asks: Sequence[Tuple[float, float]],
) -> Optional[float]:
    """
    Computes cumulative Order Flow Imbalance across quote level updates.
    Each item is (price, size). Cont, Kukanov, Stoikov (2014) formulation.
    """
    if len(bids) < 2 or len(asks) < 2:
        return None

    ofi_total = 0.0
    for i in range(1, min(len(bids), len(asks))):
        p_b_prev, q_b_prev = bids[i - 1]
        p_b_curr, q_b_curr = bids[i]
        p_a_prev, q_a_prev = asks[i - 1]
        p_a_curr, q_a_curr = asks[i]

        # Delta bid size
        if p_b_curr > p_b_prev:
            delta_q_b = q_b_curr
        elif p_b_curr == p_b_prev:
            delta_q_b = q_b_curr - q_b_prev
        else:
            delta_q_b = 0.0

        # Delta ask size
        if p_a_curr < p_a_prev:
            delta_q_a = q_a_curr
        elif p_a_curr == p_a_prev:
            delta_q_a = q_a_curr - q_a_prev
        else:
            delta_q_a = 0.0

        ofi_total += (delta_q_b - delta_q_a)

    return float(ofi_total)


def compute_vpin(
    buy_volumes: Sequence[float],
    sell_volumes: Sequence[float],
) -> Optional[float]:
    """
    Computes VPIN (Volume-Synchronized Probability of Toxicity) across volume buckets:
    VPIN = Σ |V_b - V_s| / Σ (V_b + V_s) ∈ [0, 1].
    VPIN > 0.40 indicates high toxicity / severe adverse selection.
    """
    buys = np.asarray(buy_volumes, dtype=float)
    sells = np.asarray(sell_volumes, dtype=float)
    if len(buys) == 0 or len(sells) == 0 or len(buys) != len(sells):
        return None

    total_imbalance = np.sum(np.abs(buys - sells))
    total_volume = np.sum(buys + sells)
    if total_volume <= 0:
        return None
    return float(np.clip(total_imbalance / total_volume, 0.0, 1.0))


def compute_loss_velocity(
    delta_pnl: float,
    delta_minutes: float,
) -> float:
    """
    Computes intraday loss rate d(PnL)/dt in dollars per minute.
    """
    if delta_minutes <= 0:
        return 0.0
    return float(delta_pnl / delta_minutes)


# ---------------------------------------------------------------------------
# DynamicCircuitBreaker Engine
# ---------------------------------------------------------------------------

class DynamicCircuitBreaker:
    """
    Dynamic intraday circuit breaker engine and flash liquidity guardrail.

    Evaluates:
    - Volatility Jump: 5m EWMA realized vol Z-score > 3.5 => SOFT_HALT (VOLATILITY_BURST_HALT)
    - Flash Crash Shield: OFI < -threshold AND VPIN > 0.40 => SOFT_HALT (FLASH_CRASH_SHIELD)
    - Intraday Loss Velocity Brake: d(PnL)/dt <= -(Daily Loss Limit / 30m) => HARD_HALT (LOSS_VELOCITY_BREACH)

    Parameters
    ----------
    volatility_z_threshold : float
        Z-score threshold for volatility jump trigger (default 3.5).
    vpin_threshold : float
        VPIN toxicity threshold for flash crash trigger (default 0.40).
    ofi_threshold : float
        Order Flow Imbalance negative threshold (default 1000.0).
    loss_velocity_window_mins : float
        Time window in minutes over which daily loss limit is paced (default 30.0 mins).
    daily_loss_limit_pct : float | None
        Daily loss limit fraction of equity (default from settings.DAILY_LOSS_LIMIT_PCT, e.g. 0.02).
    state_file : Path | None
        Path to persist circuit breaker state (default output/circuit_breaker_state.json).
    kill_switch : GlobalKillSwitch | None
        Kill switch instance to coordinate sentinel files with.
    """

    def __init__(
        self,
        *,
        volatility_z_threshold: Optional[float] = None,
        vpin_threshold: Optional[float] = None,
        ofi_threshold: Optional[float] = None,
        loss_velocity_window_mins: Optional[float] = None,
        daily_loss_limit_pct: Optional[float] = None,
        state_file: Optional[Path] = None,
        kill_switch: Optional[GlobalKillSwitch] = None,
    ) -> None:
        self.volatility_z_threshold = (
            volatility_z_threshold
            if volatility_z_threshold is not None
            else getattr(settings, "CIRCUIT_BREAKER_VOLATILITY_Z_THRESHOLD", 3.5)
        )
        self.vpin_threshold = (
            vpin_threshold
            if vpin_threshold is not None
            else getattr(settings, "CIRCUIT_BREAKER_VPIN_THRESHOLD", 0.40)
        )
        self.ofi_threshold = (
            ofi_threshold
            if ofi_threshold is not None
            else getattr(settings, "CIRCUIT_BREAKER_OFI_THRESHOLD", 1000.0)
        )
        self.loss_velocity_window_mins = (
            loss_velocity_window_mins
            if loss_velocity_window_mins is not None
            else getattr(settings, "CIRCUIT_BREAKER_LOSS_VELOCITY_WINDOW_MINS", 30.0)
        )
        self.daily_loss_limit_pct = (
            daily_loss_limit_pct
            if daily_loss_limit_pct is not None
            else getattr(settings, "DAILY_LOSS_LIMIT_PCT", 0.02)
        )
        self.state_file = state_file or (settings.OUTPUT_DIR / "circuit_breaker_state.json")
        self.kill_switch = kill_switch or GlobalKillSwitch()
        self._current_metrics = CircuitBreakerMetrics(state=CircuitBreakerState.NORMAL)

    @property
    def current_metrics(self) -> CircuitBreakerMetrics:
        """Return the current metrics snapshot."""
        return self._current_metrics

    @property
    def current_state(self) -> CircuitBreakerState:
        """Return the current circuit breaker lifecycle state."""
        return self._current_metrics.state

    # ------------------------------------------------------------------
    # Trigger Check Methods
    # ------------------------------------------------------------------

    def check_volatility_jump(
        self,
        intraday_returns_or_prices: Union[Sequence[float], pd.Series, np.ndarray, float],
        baseline_20d_vol: float,
        baseline_vol_std: Optional[float] = None,
        is_prices: bool = False,
        ewma_span: int = 12,
    ) -> Tuple[bool, float, Optional[str]]:
        """
        Evaluate Volatility Jump Detector.
        If Z-score > volatility_z_threshold => returns (True, z_score, reason).
        """
        if isinstance(intraday_returns_or_prices, (int, float)):
            z_score = calculate_volatility_zscore_from_vols(
                realized_vol_5m=float(intraday_returns_or_prices),
                baseline_20d_vol=baseline_20d_vol,
                baseline_vol_std=baseline_vol_std,
            )
        else:
            z_score = compute_volatility_zscore(
                intraday_returns_or_prices=intraday_returns_or_prices,
                baseline_20d_vol=baseline_20d_vol,
                baseline_vol_std=baseline_vol_std,
                is_prices=is_prices,
                ewma_span=ewma_span,
            )

        if z_score > self.volatility_z_threshold:
            reason = (
                f"VOLATILITY_BURST_HALT: 5m EWMA realized vol Z-score {z_score:.2f} > "
                f"threshold {self.volatility_z_threshold:.2f}"
            )
            return True, z_score, reason
        return False, z_score, None

    def check_flash_crash_shield(
        self,
        ofi: float,
        vpin: float,
    ) -> Tuple[bool, Optional[str]]:
        """
        Evaluate Order Flow Imbalance (OFI) & Toxicity Crash Shield.
        When OFI < -threshold AND VPIN > vpin_threshold => returns (True, reason).
        """
        if ofi < -abs(self.ofi_threshold) and vpin > self.vpin_threshold:
            reason = (
                f"FLASH_CRASH_SHIELD: Order Flow Imbalance {ofi:.1f} < -{abs(self.ofi_threshold):.1f} "
                f"and VPIN toxicity {vpin:.2f} > {self.vpin_threshold:.2f}"
            )
            return True, reason
        return False, None

    def check_loss_velocity_brake(
        self,
        loss_velocity_per_min: float,
        account_equity: float,
        daily_loss_limit_pct: Optional[float] = None,
    ) -> Tuple[bool, float, Optional[str]]:
        """
        Evaluate Intraday Loss Velocity Brake.
        When d(PnL)/dt <= -(Daily Loss Limit / 30 mins) => returns (True, threshold_velocity, reason).
        """
        limit_pct = daily_loss_limit_pct if daily_loss_limit_pct is not None else self.daily_loss_limit_pct
        daily_loss_limit_dollars = account_equity * limit_pct
        max_allowed_loss_velocity = daily_loss_limit_dollars / max(self.loss_velocity_window_mins, 1e-4)
        loss_velocity_threshold = -max_allowed_loss_velocity

        if loss_velocity_per_min <= loss_velocity_threshold:
            reason = (
                f"LOSS_VELOCITY_BREACH: Intraday loss rate ${abs(loss_velocity_per_min):.2f}/min exceeds "
                f"allowable rate ${max_allowed_loss_velocity:.2f}/min "
                f"(Daily limit ${daily_loss_limit_dollars:,.0f} / {self.loss_velocity_window_mins:.0f}m)"
            )
            return True, loss_velocity_threshold, reason
        return False, loss_velocity_threshold, None

    # ------------------------------------------------------------------
    # State Updates & Persistence
    # ------------------------------------------------------------------

    def update_metrics(
        self,
        *,
        volatility_zscore: Optional[float] = None,
        vpin: Optional[float] = None,
        ofi: Optional[float] = None,
        loss_velocity_per_min: Optional[float] = None,
        account_equity: Optional[float] = None,
        custom_state: Optional[CircuitBreakerState] = None,
        custom_reason: Optional[str] = None,
        persist: bool = True,
    ) -> CircuitBreakerMetrics:
        """
        Update circuit breaker state based on evaluated quantitative inputs.

        Evaluation Priority:
        1. Explicit custom_state (if supplied)
        2. Intraday Loss Velocity Brake -> HARD_HALT
        3. Volatility Jump Detector -> SOFT_HALT
        4. Flash Crash Shield (OFI + VPIN) -> SOFT_HALT
        5. Caution thresholds (elevated vol Z > 2.0 or VPIN > threshold) -> CAUTION
        6. Otherwise -> NORMAL
        """
        state = CircuitBreakerState.NORMAL
        reasons: List[str] = []

        if custom_state is not None:
            state = custom_state
            if custom_reason:
                reasons.append(custom_reason)
        else:
            # 1. Loss velocity brake (HARD_HALT)
            if loss_velocity_per_min is not None and account_equity is not None and account_equity > 0:
                breached, _, reason = self.check_loss_velocity_brake(loss_velocity_per_min, account_equity)
                if breached and reason:
                    state = CircuitBreakerState.HARD_HALT
                    reasons.append(reason)

            # If not HARD_HALT, check SOFT_HALT / CAUTION triggers
            if state != CircuitBreakerState.HARD_HALT:
                # 2. Volatility burst
                if volatility_zscore is not None:
                    if volatility_zscore > self.volatility_z_threshold:
                        state = CircuitBreakerState.SOFT_HALT
                        reasons.append(
                            f"VOLATILITY_BURST_HALT: Volatility Z-score {volatility_zscore:.2f} > {self.volatility_z_threshold:.2f}"
                        )
                    elif volatility_zscore > 2.0:
                        state = CircuitBreakerState.CAUTION
                        reasons.append(f"Elevated volatility Z-score: {volatility_zscore:.2f}")

                # 3. Flash crash shield (OFI & VPIN). Evaluated unconditionally
                # whenever BOTH signals are actually present -- independent of
                # OFI_SHIELD_ENABLED -- so real, dangerous data always trips
                # the shield rather than the shield being inert unless an
                # operator also opts in via the flag.
                if ofi is not None and vpin is not None:
                    fc_triggered, fc_reason = self.check_flash_crash_shield(ofi, vpin)
                    if fc_triggered and fc_reason:
                        state = CircuitBreakerState.SOFT_HALT
                        reasons.append(fc_reason)
                    elif vpin > self.vpin_threshold:
                        if state != CircuitBreakerState.SOFT_HALT:
                            state = CircuitBreakerState.CAUTION
                            reasons.append(f"Elevated VPIN toxicity: {vpin:.2f}")
                elif getattr(settings, "OFI_SHIELD_ENABLED", False) and vpin is None:
                    # Fail closed only on a genuine VPIN data gap. OFI is
                    # architecturally never supplied by the daemon's one live
                    # caller (see docs/architecture/execution.md's
                    # dynamic_circuit_breaker.py entry) -- failing closed on
                    # OFI's routine absence alone would make this flag
                    # permanently SOFT_HALT every tick the moment it's
                    # enabled, which is not a targeted response to missing
                    # data, it's a structural, permanent halt.
                    state = CircuitBreakerState.SOFT_HALT
                    reasons.append("FLASH_CRASH_SHIELD_HALT (FAIL CLOSED): Missing data for VPIN")
                elif vpin is not None and vpin > self.vpin_threshold:
                    if state != CircuitBreakerState.SOFT_HALT:
                        state = CircuitBreakerState.CAUTION
                        reasons.append(f"Elevated VPIN toxicity: {vpin:.2f}")

        now_str = datetime.now(timezone.utc).isoformat()
        final_reason = "; ".join(reasons) if reasons else "Normal market conditions"

        metrics = CircuitBreakerMetrics(
            state=state,
            volatility_zscore=volatility_zscore,
            vpin=vpin,
            ofi=ofi,
            loss_velocity_per_min=loss_velocity_per_min,
            reason=final_reason,
            updated_at=now_str,
        )
        self._current_metrics = metrics

        # Synchronize sentinel files with GlobalKillSwitch only when persistence is enabled
        if persist:
            if state == CircuitBreakerState.HARD_HALT:
                self.kill_switch.activate(reason=final_reason)
            elif state == CircuitBreakerState.SOFT_HALT:
                self.kill_switch.activate_soft_halt(reason=final_reason)
            elif state in (CircuitBreakerState.NORMAL, CircuitBreakerState.CAUTION):
                if self.kill_switch.is_soft_halt_active():
                    self.kill_switch.deactivate_soft_halt()
            self.record_metrics(metrics)

        return metrics

    def record_metrics(self, metrics: Optional[CircuitBreakerMetrics] = None) -> None:
        """Persist metrics atomically to circuit_breaker_state.json."""
        m = metrics or self._current_metrics
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(m.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(self.state_file)
        logger.debug("Recorded circuit breaker state: %s to %s", m.state.value, self.state_file)

    def load_metrics(self) -> Optional[CircuitBreakerMetrics]:
        """Load circuit breaker metrics from circuit_breaker_state.json."""
        if not self.state_file.exists():
            return None
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            m = CircuitBreakerMetrics.from_dict(data)
            self._current_metrics = m
            return m
        except Exception as exc:
            logger.debug("Failed to load circuit breaker metrics (%s)", exc)
            return None

    def evaluate_order_intent(self, intent: OrderIntent) -> Tuple[bool, Optional[str]]:
        """
        Evaluate an OrderIntent against the current circuit breaker state.

        Rules:
        - HARD_HALT: Blocks all order submissions.
        - SOFT_HALT: Blocks risk-increasing BUY orders; permits risk-reducing SELL orders.
        - CAUTION / NORMAL: Permits all order submissions.
        """
        state = self._current_metrics.state
        reason = self._current_metrics.reason or "Circuit breaker active"

        if state == CircuitBreakerState.HARD_HALT:
            return False, f"HARD_HALT active: {reason} — all order submissions blocked"

        if state == CircuitBreakerState.SOFT_HALT:
            if intent.side == OrderSide.BUY:
                return False, f"SOFT_HALT active: {reason} — risk-increasing BUY orders blocked"
            return True, f"Permitted: risk-reducing SELL order allowed under SOFT_HALT ({reason})"

        return True, None

    def reset(self) -> None:
        """Reset circuit breaker state to NORMAL and clean up state/sentinel files."""
        self._current_metrics = CircuitBreakerMetrics(
            state=CircuitBreakerState.NORMAL,
            reason="Reset to NORMAL",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        if self.state_file.exists():
            try:
                self.state_file.unlink()
            except OSError:
                pass
        if self.kill_switch.is_soft_halt_active():
            self.kill_switch.deactivate_soft_halt()

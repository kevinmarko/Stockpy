"""
pilots/zero_dte_engine.py — 0DTE Intraday Momentum & Volatility Breakout Desk.
=============================================================================

Institutional 0DTE options execution, breakout scanning, and fast risk lifecycle management engine.

Key Capabilities:
1. 15-Minute Opening Range Breakout (ORB):
    Computes opening range boundaries [Low_15, High_15] from 9:30 to 9:45 AM ET.
    Detects directional thrust above High_15 (Bullish Breakout) or below Low_15 (Bearish Breakdown).

2. TTM Volatility Squeeze Gate:
    Bollinger Bands (20-period, 2.0 std) contracting within Keltner Channels (20-period, 1.5 ATR).
    Identifies pre-breakout volatility compression and fires upon expansion/momentum release.

3. High-Gamma Convexity Contract Selection:
    Selects optimal ATM/1-OTM 0DTE strike (Delta ~ 0.40 - 0.55) expiring the current trading session.

4. Fast 0DTE Risk Lifecycle & Pin Protection:
    - Fast Profit Target (+75% gain in premium) -> PROFIT_TARGET_75 (EXIT_PROFIT_TARGET)
    - Fast Stop Loss (-30% loss or opening range reversal) -> STOP_LOSS_30 (EXIT_STOP_LOSS)
    - Mandatory 15:45 ET Hard Time Stop to eliminate closing assignment/pin risk -> HARD_TIME_STOP_1545 (EXIT_HARD_TIME_STOP).

5. Single-Leg 0DTE Paper Broker Execution:
    Submits single-leg option orders tagged with strategy_name="0DTE Momentum Breakout" into PaperAccountStore.

Design Invariants:
* **AST-Safe (CONSTRAINTS #1 & #3)**: Pure compute/read module. Never imports heavy engines.
* **Honesty (CONSTRAINT #4)**: Exact cash impacts, Black-Scholes Greeks, zero fabricated prices.
* **Never Raises (CONSTRAINT #6)**: Degrades gracefully on missing or closed market data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import logging
import math
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple, Union
import uuid
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from data.paper_account_store import PaperAccountStore, PaperOrder, PaperPosition
from settings import settings


logger = logging.getLogger(__name__)

# US equity/options market timezone. The 0DTE hard-exit-time rule (e.g. "15:45") is always
# quoted in ET (see module docstring / OPTIONS_0DTE_HARD_EXIT_TIME) -- comparing it against a
# naive `datetime.now(timezone.utc).time()` would compare a UTC wall-clock time against an ET
# threshold, silently firing the hard stop hours early (EDT, UTC-4) or late (EST, UTC-5).
_ET = ZoneInfo("America/New_York")

__all__ = [
    "compute_opening_range",
    "detect_volatility_squeeze",
    "scan_0dte_breakouts",
    "get_0dte_signals",
    "evaluate_0dte_exits",
    "execute_0dte_trade",
    "execute_0dte_exits",
    "parse_option_symbol",
    "parse_chain_data",
    "OpeningRange",
    "SqueezeResult",
    "ZeroDteContract",
    "ZeroDteBreakoutSignal",
    "ZeroDteExitSignal",
    "DEFAULT_0DTE_SYMBOLS",
]

DEFAULT_0DTE_SYMBOLS = ["SPY", "QQQ", "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META"]

_OPTION_SYM_RE = re.compile(
    r"^(?P<ticker>[A-Z0-9]+)\s+(?P<exp>\d{4}-\d{2}-\d{2})\s+\$(?P<strike>\d+(?:\.\d+)?)\s+(?P<type>CALL|PUT)$",
    re.IGNORECASE,
)

_DEGENERATE_THRESHOLD = 1e-12
_DEFAULT_MULTIPLIER = 100.0


@dataclass
class OpeningRange:
    """Represents the intraday opening range high, low, and volume baseline."""
    high: float = 0.0
    low: float = 0.0
    range_width: float = 0.0
    range_span: float = 0.0
    vwap: float = 0.0
    volume: float = 0.0
    avg_volume: float = 0.0
    range_minutes: int = 15
    bars_count: int = 0
    valid: bool = False
    is_valid: bool = False

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        elif item == "range_size":
            return self.range_width
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        try:
            return self[item]
        except KeyError:
            return default

    def to_dict(self) -> Dict[str, Any]:
        return {
            "high": self.high,
            "low": self.low,
            "range_width": self.range_width,
            "range_span": self.range_span,
            "range_size": self.range_width,
            "vwap": self.vwap,
            "volume": self.volume,
            "avg_volume": self.avg_volume,
            "range_minutes": self.range_minutes,
            "bars_count": self.bars_count,
            "valid": self.valid,
            "is_valid": self.is_valid,
        }


@dataclass
class SqueezeResult:
    """TTM Squeeze volatility compression indicators."""
    squeeze_on: bool = False
    squeeze_fired: bool = False
    is_in_squeeze: bool = False
    direction: str = "NEUTRAL"
    momentum: float = 0.0
    status: str = "NO_DATA"
    compression_ratio: float = 1.0
    bb_upper: float = 0.0
    bb_lower: float = 0.0
    kc_upper: float = 0.0
    kc_lower: float = 0.0

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        elif item == "in_squeeze":
            return self.squeeze_on
        elif item == "squeeze_released":
            return self.squeeze_fired
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        try:
            return self[item]
        except KeyError:
            return default

    def to_dict(self) -> Dict[str, Any]:
        return {
            "squeeze_on": self.squeeze_on,
            "squeeze_fired": self.squeeze_fired,
            "is_in_squeeze": self.is_in_squeeze,
            "direction": self.direction,
            "momentum": self.momentum,
            "status": self.status,
            "compression_ratio": self.compression_ratio,
            "bb_upper": self.bb_upper,
            "bb_lower": self.bb_lower,
            "kc_upper": self.kc_upper,
            "kc_lower": self.kc_lower,
        }


@dataclass
class ZeroDteContract:
    """Represents a candidate 0DTE option contract."""
    symbol: str
    contract_symbol: str
    underlying: str
    strike: float
    option_type: str  # "CALL" | "PUT"
    expiration: str
    dte: float = 0.0
    delta: float = 0.50
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    mid_price: float = 0.0
    estimated_cost: float = 0.0

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        try:
            return self[item]
        except KeyError:
            return default

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "contract_symbol": self.contract_symbol,
            "underlying": self.underlying,
            "strike": self.strike,
            "option_type": self.option_type,
            "expiration": self.expiration,
            "dte": self.dte,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "bid": self.bid,
            "ask": self.ask,
            "mid_price": self.mid_price,
            "estimated_cost": self.estimated_cost,
        }


@dataclass
class ZeroDteBreakoutSignal:
    """Actionable 0DTE Breakout Signal."""
    symbol: str
    signal_type: str  # "BULLISH_BREAKOUT" | "BEARISH_BREAKDOWN" | "NO_SIGNAL"
    action: str  # "BUY_CALL" | "BUY_PUT" | "NO_ACTION"
    current_price: float
    orb_high: float
    orb_low: float
    confidence: float
    selected_contract: Optional[Dict[str, Any]] = None
    contract_obj: Optional[ZeroDteContract] = None
    squeeze_result: Optional[SqueezeResult] = None
    opening_range: Optional[OpeningRange] = None
    volume_surge: bool = False
    reason: str = ""

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        elif item == "direction":
            if self.signal_type == "BULLISH_BREAKOUT":
                return "BULLISH"
            elif self.signal_type == "BEARISH_BREAKDOWN":
                return "BEARISH"
            return "NEUTRAL"
        elif item == "spot_price":
            return self.current_price
        elif item == "spot":
            return self.current_price
        elif item == "is_actionable":
            return self.action != "NO_ACTION"
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        try:
            return self[item]
        except KeyError:
            return default

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "signal_type": self.signal_type,
            "action": self.action,
            "current_price": self.current_price,
            "orb_high": self.orb_high,
            "orb_low": self.orb_low,
            "confidence": self.confidence,
            "selected_contract": self.selected_contract,
            "volume_surge": self.volume_surge,
            "reason": self.reason,
        }


@dataclass
class ZeroDteExitSignal:
    """Exit directive generated by 0DTE fast risk lifecycle evaluator."""
    exit_type: str  # "EXIT_HARD_TIME_STOP" | "EXIT_PROFIT_TARGET" | "EXIT_STOP_LOSS"
    exit_reason: str  # "HARD_TIME_STOP_1545" | "PROFIT_TARGET_75" | "STOP_LOSS_30"
    symbol: str
    contract_symbol: str
    position_id: str
    entry_price: float
    current_price: float
    pnl_pct: float
    unrealized_pl: float
    quantity: float
    urgent: bool
    side: str = "sell"
    action: str = "CLOSE"
    trigger: str = "HARD_TIME_STOP"
    reason_detail: str = ""
    contracts: int = 1

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        elif item == "position_symbol":
            return self.contract_symbol
        elif item == "qty":
            return self.quantity
        elif item == "reason":
            return self.reason_detail or self.exit_reason
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        try:
            return self[item]
        except KeyError:
            return default

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exit_type": self.exit_type,
            "exit_reason": self.exit_reason,
            "symbol": self.symbol,
            "contract_symbol": self.contract_symbol,
            "position_symbol": self.contract_symbol,
            "position_id": self.position_id,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "pnl_pct": self.pnl_pct,
            "unrealized_pl": self.unrealized_pl,
            "quantity": self.quantity,
            "qty": self.quantity,
            "contracts": self.contracts,
            "urgent": self.urgent,
            "side": self.side,
            "action": self.action,
            "trigger": self.trigger,
            "reason_detail": self.reason_detail,
            "reason": self.reason_detail or self.exit_reason,
        }


def parse_option_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """Parses standardized option leg symbol e.g. 'SPY 2026-08-14 $500.00 CALL'."""
    if not isinstance(symbol, str):
        return None
    m = _OPTION_SYM_RE.match(symbol.strip())
    if not m:
        return None
    return {
        "ticker": m.group("ticker").upper(),
        "expiration": m.group("exp"),
        "strike": float(m.group("strike")),
        "option_type": m.group("type").upper(),
    }


def compute_opening_range(
    intraday_bars: Union[pd.DataFrame, Sequence[Dict[str, Any]], Sequence[float]],
    range_minutes: int = 15,
) -> OpeningRange:
    """
    Computes opening range boundaries (High, Low, VWAP, Volume) over the initial opening minutes.
    Enforces degenerate guards on empty or single-bar sequences.
    """
    if intraday_bars is None:
        return OpeningRange(valid=False, is_valid=False)

    if isinstance(intraday_bars, pd.DataFrame):
        if intraday_bars.empty:
            return OpeningRange(valid=False, is_valid=False)
        bars_count = min(len(intraday_bars), range_minutes)
        bars_slice = intraday_bars.iloc[:bars_count]
        high_val = float(bars_slice["high"].max()) if "high" in bars_slice.columns else float(bars_slice["close"].max())
        low_val = float(bars_slice["low"].min()) if "low" in bars_slice.columns else float(bars_slice["close"].min())
        vol = float(bars_slice["volume"].sum()) if "volume" in bars_slice.columns else 0.0

        if "close" in bars_slice.columns and "volume" in bars_slice.columns and vol > 0:
            typical_price = (bars_slice["high"] + bars_slice["low"] + bars_slice["close"]) / 3.0
            vwap = float((typical_price * bars_slice["volume"]).sum() / vol)
        elif "close" in bars_slice.columns:
            vwap = float(bars_slice["close"].mean())
        else:
            vwap = (high_val + low_val) / 2.0

    elif isinstance(intraday_bars, Sequence) and len(intraday_bars) > 0:
        bars_count = min(len(intraday_bars), range_minutes)
        first_item = intraday_bars[0]
        if isinstance(first_item, dict):
            bars_slice = intraday_bars[:bars_count]
            highs = [float(b.get("high", b.get("close", 0.0))) for b in bars_slice]
            lows = [float(b.get("low", b.get("close", 0.0))) for b in bars_slice]
            vols = [float(b.get("volume", 0.0)) for b in bars_slice]
            high_val = max(highs) if highs else 0.0
            low_val = min(lows) if lows else 0.0
            vol = float(sum(vols)) if vols else 0.0
            vwap = (high_val + low_val) / 2.0
        else:
            vals = [float(x) for x in intraday_bars[:bars_count]]
            high_val = max(vals) if vals else 0.0
            low_val = min(vals) if vals else 0.0
            vol = 0.0
            vwap = (high_val + low_val) / 2.0
    else:
        return OpeningRange(valid=False, is_valid=False)

    if high_val <= 0 or low_val <= 0 or high_val < low_val or bars_count == 0:
        return OpeningRange(valid=False, is_valid=False)

    width = high_val - low_val
    return OpeningRange(
        high=round(high_val, 4),
        low=round(low_val, 4),
        range_width=round(width, 4),
        range_span=round(width, 4),
        vwap=round(vwap, 4),
        volume=round(vol, 2),
        avg_volume=round(vol / max(1, bars_count), 2),
        range_minutes=range_minutes,
        bars_count=bars_count,
        valid=True,
        is_valid=True,
    )


def detect_volatility_squeeze(
    bars: Union[pd.DataFrame, Sequence[Dict[str, Any]], Sequence[float]],
    bb_period: int = 20,
    bb_std: float = 2.0,
    kc_period: int = 20,
    kc_mult: float = 1.5,
) -> SqueezeResult:
    """
    Detects John Carter's TTM Squeeze: Bollinger Bands contracting inside Keltner Channels.
    """
    if bars is None:
        return SqueezeResult(status="NO_DATA")

    if isinstance(bars, pd.DataFrame):
        if len(bars) < max(5, min(bb_period, kc_period) // 2):
            return SqueezeResult(status="NO_DATA")
        close = bars["close"].astype(float)
        high = bars["high"].astype(float) if "high" in bars.columns else close
        low = bars["low"].astype(float) if "low" in bars.columns else close
    elif isinstance(bars, Sequence) and len(bars) > 0:
        if isinstance(bars[0], dict):
            close = pd.Series([float(b.get("close", 0.0)) for b in bars])
            high = pd.Series([float(b.get("high", b.get("close", 0.0))) for b in bars])
            low = pd.Series([float(b.get("low", b.get("close", 0.0))) for b in bars])
        else:
            close = pd.Series([float(x) for x in bars])
            high = close
            low = close
    else:
        return SqueezeResult(status="NO_DATA")

    n = len(close)
    if n < 5:
        return SqueezeResult(status="NO_DATA")

    # 1. Bollinger Bands
    effective_bb = min(bb_period, n)
    sma = close.rolling(effective_bb).mean().fillna(close)
    std = close.rolling(effective_bb).std(ddof=0).fillna(0.0)
    bb_upper = sma + (bb_std * std)
    bb_lower = sma - (bb_std * std)

    # 2. Keltner Channels (EMA + ATR)
    effective_kc = min(kc_period, n)
    ema = close.ewm(span=effective_kc, adjust=False).mean()
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).fillna(0.0)
    atr = tr.rolling(effective_kc).mean().fillna(tr)
    kc_upper = ema + (kc_mult * atr)
    kc_lower = ema - (kc_mult * atr)

    # Squeeze condition: BB inside KC
    bb_w = bb_upper - bb_lower
    kc_w = kc_upper - kc_lower
    compression_ratio = float((bb_w.iloc[-1] / kc_w.iloc[-1])) if kc_w.iloc[-1] > 0 else 1.0

    squeeze_series = (bb_upper <= kc_upper + 1e-4) & (bb_lower >= kc_lower - 1e-4)
    squeeze_on = bool(squeeze_series.iloc[-1])

    # Check if squeeze released in recent bars
    prev_squeeze = bool(squeeze_series.iloc[-2]) if len(squeeze_series) >= 2 else squeeze_on
    squeeze_fired = bool(prev_squeeze and not squeeze_on)

    midpoint = (sma.iloc[-1] + ema.iloc[-1]) / 2.0
    momentum = float(round(close.iloc[-1] - midpoint, 4))

    if squeeze_on:
        status = "SQUEEZE_ON"
        direction = "NEUTRAL"
    elif squeeze_fired or not squeeze_on:
        if momentum > 0:
            status = "SQUEEZE_RELEASE_BULLISH"
            direction = "BULLISH"
            squeeze_fired = True
        elif momentum < 0:
            status = "SQUEEZE_RELEASE_BEARISH"
            direction = "BEARISH"
            squeeze_fired = True
        else:
            status = "NO_SQUEEZE"
            direction = "NEUTRAL"
    else:
        status = "NO_SQUEEZE"
        direction = "NEUTRAL"

    return SqueezeResult(
        squeeze_on=squeeze_on,
        squeeze_fired=squeeze_fired,
        is_in_squeeze=squeeze_on,
        direction=direction,
        momentum=momentum,
        status=status,
        compression_ratio=round(compression_ratio, 4),
        bb_upper=round(float(bb_upper.iloc[-1]), 4),
        bb_lower=round(float(bb_lower.iloc[-1]), 4),
        kc_upper=round(float(kc_upper.iloc[-1]), 4),
        kc_lower=round(float(kc_lower.iloc[-1]), 4),
    )


def parse_chain_data(
    chain_data: Any,
    underlying: str,
    target_type: str = "CALL",
    spot_price: Optional[float] = None,
    min_delta: float = 0.40,
    max_delta: float = 0.55,
) -> Optional[Dict[str, Any]]:
    """
    Finds the optimal 0DTE option contract matching delta range [0.40, 0.55] from chain data.

    Returns None (never a fabricated synthetic contract -- CONSTRAINT #4) when no real chain
    data is supplied, or no contract of the target type can be found in it. A caller must treat
    a missing chain as "no tradable contract", never as license to invent a bid/ask/delta and
    route it into a live BUY_CALL/BUY_PUT signal.
    """
    if not chain_data:
        return None

    target_type = target_type.upper()
    candidates = []

    for item in chain_data:
        c_type = str(item.get("option_type", item.get("type", ""))).upper()
        if c_type != target_type:
            continue

        raw_delta = float(item.get("delta", 0.0))
        abs_delta = abs(raw_delta)

        # Check delta in target band
        if min_delta <= abs_delta <= max_delta:
            candidates.append(item)

    if not candidates:
        # Fallback to closest delta
        matching_type = [c for c in chain_data if str(c.get("option_type", c.get("type", ""))).upper() == target_type]
        if matching_type:
            target_val = 0.48
            candidates = sorted(matching_type, key=lambda c: abs(abs(float(c.get("delta", 0.5))) - target_val))

    if candidates:
        best = candidates[0]
        raw_strike = best.get("strike")
        if raw_strike is None:
            raw_strike = spot_price
        if raw_strike is None:
            # No real strike on the matched contract and no spot_price to fall back to --
            # refuse rather than report a strike of 0.0/None as if it were real (CONSTRAINT #4).
            logger.debug(
                "parse_chain_data: matched contract for %s has no strike and no spot_price "
                "fallback; skipping.", underlying,
            )
            return None
        bid = float(best.get("bid", 0.0))
        ask = float(best.get("ask", 0.0))
        mid = (bid + ask) / 2.0 if (bid + ask) > 0 else float(best.get("lastPrice", best.get("price", 1.0)))
        return {
            "symbol": best.get("symbol", underlying),
            "contract_symbol": best.get("contract_symbol", best.get("symbol", f"{underlying}-0DTE")),
            "underlying": underlying,
            "strike": float(raw_strike),
            "option_type": target_type,
            "expiration": best.get("expiration", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            "dte": float(best.get("dte", 0.0)),
            "delta": float(best.get("delta", 0.50 if target_type == "CALL" else -0.50)),
            "bid": bid,
            "ask": ask,
            "mid_price": round(mid, 4),
            "estimated_cost": round(mid * _DEFAULT_MULTIPLIER, 2),
        }

    return None


def scan_0dte_breakouts(
    symbol: str,
    intraday_bars: Optional[Union[pd.DataFrame, Sequence[Dict[str, Any]]]] = None,
    current_quote: Optional[Union[float, Dict[str, Any]]] = None,
    chain_data: Optional[Any] = None,
    range_minutes: int = 15,
    volume_threshold_mult: float = 1.25,
) -> ZeroDteBreakoutSignal:
    """
    Evaluates Opening Range Breakout and TTM squeeze to produce actionable 0DTE trade signals.
    """
    sym = (symbol or "SPY").upper().strip()

    # Spot price resolution
    spot = 0.0
    if isinstance(current_quote, dict) and "price" in current_quote:
        spot = float(current_quote["price"])
    elif isinstance(current_quote, (int, float)) and current_quote > 0:
        spot = float(current_quote)
    elif isinstance(intraday_bars, pd.DataFrame) and not intraday_bars.empty and "close" in intraday_bars.columns:
        spot = float(intraday_bars["close"].iloc[-1])
    elif isinstance(intraday_bars, Sequence) and len(intraday_bars) > 0:
        last = intraday_bars[-1]
        spot = float(last.get("close", 0.0)) if isinstance(last, dict) else float(last)
    else:
        try:
            from pilots.price_provider import get_current_price
            p = get_current_price(sym)
            if p and p > 0:
                spot = p
        except Exception:
            pass

    if spot <= 0:
        # No real quote resolvable for this symbol from any source -- refuse to fabricate one
        # (CONSTRAINT #4; matches `pilots/options_hedging.py::execute_delta_hedge`'s "refuse
        # rather than fabricate") and generate a live BUY_CALL/BUY_PUT signal off an invented
        # price. Degrade honestly to NO_SIGNAL instead.
        return ZeroDteBreakoutSignal(
            symbol=sym,
            signal_type="NO_SIGNAL",
            action="NO_ACTION",
            current_price=0.0,
            orb_high=0.0,
            orb_low=0.0,
            confidence=0.0,
            selected_contract=None,
            squeeze_result=SqueezeResult(status="NO_DATA"),
            opening_range=OpeningRange(valid=False, is_valid=False),
            volume_surge=False,
            reason=f"No live quote available for {sym}; refusing to generate a signal from a fabricated spot price.",
        )

    # Opening range and squeeze
    if intraday_bars is not None:
        orb = compute_opening_range(intraday_bars, range_minutes=range_minutes)
        squeeze = detect_volatility_squeeze(intraday_bars)
        if isinstance(intraday_bars, pd.DataFrame) and "volume" in intraday_bars.columns and len(intraday_bars) > 0:
            vol_avg = float(intraday_bars["volume"].tail(20).mean())
            cur_vol = float(intraday_bars["volume"].iloc[-1])
            vol_surge = cur_vol >= volume_threshold_mult * vol_avg
        elif isinstance(intraday_bars, Sequence) and len(intraday_bars) > 0 and isinstance(intraday_bars[0], dict):
            vols = [float(b.get("volume", 0.0)) for b in intraday_bars]
            vol_avg = float(np.mean(vols)) if vols else 1.0
            cur_vol = float(intraday_bars[-1].get("volume", vol_avg))
            vol_surge = cur_vol >= volume_threshold_mult * vol_avg
        else:
            vol_surge = True
    else:
        # No intraday bars supplied at all -- there is no real opening range or squeeze data
        # to evaluate. Degrade honestly (CONSTRAINT #4) instead of fabricating a synthetic
        # range derived from spot alone (spot*1.003/spot*0.997 -- which, since it always
        # brackets spot by construction, could never actually register a breakout, but still
        # reported fabricated squeeze_fired/momentum metadata as if it were real).
        orb = OpeningRange(valid=False, is_valid=False)
        squeeze = SqueezeResult(status="NO_DATA")
        vol_surge = False

    orb_high = orb.high
    orb_low = orb.low

    if not orb.valid:
        return ZeroDteBreakoutSignal(
            symbol=sym,
            signal_type="NO_SIGNAL",
            action="NO_ACTION",
            current_price=round(spot, 4),
            orb_high=0.0,
            orb_low=0.0,
            confidence=0.0,
            selected_contract=None,
            squeeze_result=squeeze,
            opening_range=orb,
            volume_surge=False,
            reason="No intraday opening-range bar data available; cannot evaluate a breakout.",
        )

    bullish = spot > orb_high and orb.valid
    bearish = spot < orb_low and orb.valid

    if bullish:
        sig_type = "BULLISH_BREAKOUT"
        selected = parse_chain_data(chain_data, underlying=sym, target_type="CALL", spot_price=spot)
        conf = min(0.95, 0.65 + (0.15 if vol_surge else 0.0) + (0.10 if squeeze.squeeze_fired else 0.0))
        if selected is None:
            # A real breakout was detected, but no real options chain data was available to
            # select a contract -- refuse to fabricate one (CONSTRAINT #4) and report
            # non-actionable rather than silently inventing a bid/ask/delta to trade against.
            action = "NO_ACTION"
            conf = 0.0
            reason = (
                f"Bullish breakout above 15m ORB High ${orb_high:.2f} (Current: ${spot:.2f}) but "
                "no real options chain data was available to select a contract; refusing to "
                "fabricate one."
            )
        else:
            action = "BUY_CALL"
            reason = f"Bullish breakout above 15m ORB High ${orb_high:.2f} (Current: ${spot:.2f})"
    elif bearish:
        sig_type = "BEARISH_BREAKDOWN"
        selected = parse_chain_data(chain_data, underlying=sym, target_type="PUT", spot_price=spot)
        conf = min(0.95, 0.65 + (0.15 if vol_surge else 0.0) + (0.10 if squeeze.squeeze_fired else 0.0))
        if selected is None:
            action = "NO_ACTION"
            conf = 0.0
            reason = (
                f"Bearish breakdown below 15m ORB Low ${orb_low:.2f} (Current: ${spot:.2f}) but "
                "no real options chain data was available to select a contract; refusing to "
                "fabricate one."
            )
        else:
            action = "BUY_PUT"
            reason = f"Bearish breakdown below 15m ORB Low ${orb_low:.2f} (Current: ${spot:.2f})"
    else:
        sig_type = "NO_SIGNAL"
        action = "NO_ACTION"
        selected = None
        conf = 0.0
        reason = f"Price ${spot:.2f} within opening range [${orb_low:.2f}, ${orb_high:.2f}]"

    return ZeroDteBreakoutSignal(
        symbol=sym,
        signal_type=sig_type,
        action=action,
        current_price=round(spot, 4),
        orb_high=round(orb_high, 4),
        orb_low=round(orb_low, 4),
        confidence=round(conf, 2),
        selected_contract=selected,
        squeeze_result=squeeze,
        opening_range=orb,
        volume_surge=vol_surge,
        reason=reason,
    )


def get_0dte_signals(
    symbol: str = "SPY",
    range_minutes: int = 15,
) -> Dict[str, Any]:
    """
    Retrieves current 0DTE momentum signals and opening range status for the symbol.

    Gated by `settings.OPTIONS_0DTE_ENABLED` (default False -- this is the master switch for
    "automated 0DTE options momentum breakout trading and lifecycle management" per its own
    settings.py description). When disabled, returns an honest `signal="DISABLED"` /
    `is_actionable=False` response instead of scanning and surfacing a live breakout signal.
    """
    sym = (symbol or "SPY").upper().strip()

    if not bool(getattr(settings, "OPTIONS_0DTE_ENABLED", False)):
        return {
            "symbol": sym,
            "spot": 0.0,
            "signal": "DISABLED",
            "direction": "NEUTRAL",
            "action": "NO_ACTION",
            "is_actionable": False,
            "reason": "0DTE momentum breakout trading is disabled (settings.OPTIONS_0DTE_ENABLED=False).",
            "opening_high": 0.0,
            "opening_low": 0.0,
            "opening_range": OpeningRange(valid=False, is_valid=False).to_dict(),
            "squeeze": SqueezeResult(status="NO_DATA").to_dict(),
            "candidate_contract": None,
            "selected_contract": None,
            "confidence": 0.0,
            "risk_parameters": {
                "profit_target_pct": getattr(settings, "OPTIONS_0DTE_PROFIT_TARGET_PCT", 0.75),
                "stop_loss_pct": getattr(settings, "OPTIONS_0DTE_STOP_LOSS_PCT", 0.30),
                "hard_exit_time": getattr(settings, "OPTIONS_0DTE_HARD_EXIT_TIME", "15:45"),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    bars = None
    try:
        from data.historical_store import HistoricalStore
        store = HistoricalStore()
        bars = store.get_intraday_bars(sym) if hasattr(store, "get_intraday_bars") else None
    except Exception as exc:
        logger.debug("0DTE Historical store bars lookup error: %s", exc)

    res = scan_0dte_breakouts(
        symbol=sym,
        intraday_bars=bars,
        range_minutes=range_minutes,
    )
    return {
        "symbol": res.symbol,
        "spot": res.current_price,
        "signal": res.signal_type,
        "direction": "BULLISH" if "BULLISH" in res.signal_type else ("BEARISH" if "BEARISH" in res.signal_type else "NEUTRAL"),
        "action": res.action,
        "is_actionable": bool(res.action != "NO_ACTION"),
        "reason": res.reason,
        "opening_high": res.orb_high,
        "opening_low": res.orb_low,
        "opening_range": res.opening_range.to_dict() if hasattr(res.opening_range, "to_dict") else res.opening_range,
        "squeeze": res.squeeze_result.to_dict() if hasattr(res.squeeze_result, "to_dict") else res.squeeze_result,
        "candidate_contract": res.selected_contract,
        "selected_contract": res.selected_contract,
        "confidence": res.confidence,
        "risk_parameters": {
            "profit_target_pct": getattr(settings, "OPTIONS_0DTE_PROFIT_TARGET_PCT", 0.75),
            "stop_loss_pct": getattr(settings, "OPTIONS_0DTE_STOP_LOSS_PCT", 0.30),
            "hard_exit_time": getattr(settings, "OPTIONS_0DTE_HARD_EXIT_TIME", "15:45"),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }



def _to_et_time(dt: datetime) -> time:
    """Returns `dt`'s wall-clock time in US/Eastern. A tz-aware `dt` is converted; a naive `dt`
    is assumed to already express ET wall-clock (this module's/callers' existing convention --
    e.g. `current_time_str="15:45"` in tests -- for an explicit, caller-supplied value)."""
    if dt.tzinfo is not None:
        return dt.astimezone(_ET).time()
    return dt.time()


def _parse_time_value(time_input: Optional[Union[str, datetime, time]]) -> Optional[time]:
    """Parses various time representations into standard datetime.time (US/Eastern wall-clock
    -- see `_to_et_time`)."""
    if time_input is None:
        return None
    if isinstance(time_input, time):
        return time_input
    if isinstance(time_input, datetime):
        return _to_et_time(time_input)
    if isinstance(time_input, str):
        t_str = time_input.strip()
        # Handle ISO strings like "2026-08-14T11:00:00"
        if "T" in t_str:
            try:
                return _to_et_time(datetime.fromisoformat(t_str))
            except Exception:
                t_str = t_str.split("T")[1]
        try:
            parts = t_str.split(":")
            if len(parts) >= 2:
                hour = int(parts[0])
                minute = int(parts[1][:2])
                second = int(parts[2][:2]) if len(parts) > 2 else 0
                return time(hour, minute, second)
        except Exception:
            pass
        try:
            return datetime.strptime(t_str, "%H:%M").time()
        except Exception:
            pass
    return None


def evaluate_0dte_exits(
    positions: Optional[Union[Sequence[Any], PaperAccountStore]] = None,
    current_time: Optional[Union[str, datetime, time]] = None,
    current_quotes: Optional[Union[Dict[str, float], Any]] = None,
    *,
    profit_target_pct: Optional[float] = None,
    stop_loss_pct: Optional[float] = None,
    hard_exit_time: Optional[str] = None,
    hard_exit_time_str: Optional[str] = None,
    current_time_str: Optional[Union[str, datetime, time]] = None,
    current_time_et: Optional[str] = None,
) -> List[ZeroDteExitSignal]:
    """
    Evaluates open 0DTE option positions against fast risk lifecycle management rules:
    
    1. **Hard Time Stop**: If current_time >= OPTIONS_0DTE_HARD_EXIT_TIME (e.g. 15:45 ET),
       triggers immediate market order closure with exit_reason="HARD_TIME_STOP_1545" (exit_type="EXIT_HARD_TIME_STOP").
    2. **Profit Target**: If P&L >= +75% (or configured OPTIONS_0DTE_PROFIT_TARGET_PCT),
       triggers profit target exit with exit_reason="PROFIT_TARGET_75" (exit_type="EXIT_PROFIT_TARGET").
    3. **Stop Loss**: If P&L <= -30% (or configured OPTIONS_0DTE_STOP_LOSS_PCT),
       triggers stop loss exit with exit_reason="STOP_LOSS_30" (exit_type="EXIT_STOP_LOSS").

    Returns a list of ZeroDteExitSignal objects ready for closing execution.
    """
    if profit_target_pct is None:
        profit_target_pct = float(getattr(settings, "OPTIONS_0DTE_PROFIT_TARGET_PCT", 0.75))
    if stop_loss_pct is None:
        stop_loss_pct = float(getattr(settings, "OPTIONS_0DTE_STOP_LOSS_PCT", 0.30))

    raw_hard_time = hard_exit_time or hard_exit_time_str or getattr(settings, "OPTIONS_0DTE_HARD_EXIT_TIME", "15:45")

    # Resolve positions list
    if hasattr(positions, "get_open_positions"):
        pos_list = positions.get_open_positions()
    elif isinstance(positions, Sequence):
        pos_list = list(positions)
    else:
        pos_list = []


    if not pos_list:
        return []

    # Parse current time
    effective_time_input = current_time if current_time is not None else (current_time_str if current_time_str is not None else current_time_et)
    parsed_current_time = _parse_time_value(effective_time_input)
    if parsed_current_time is None:
        # No explicit current_time supplied -- resolve "now" in US/Eastern (never bare UTC;
        # the hard-exit-time threshold below, e.g. "15:45", is always quoted in ET).
        parsed_current_time = datetime.now(_ET).time()

    # Parse hard exit time
    parsed_hard_time = _parse_time_value(raw_hard_time) or time(15, 45)

    is_hard_stop_active = parsed_current_time >= parsed_hard_time
    hard_stop_reason_code = f"HARD_TIME_STOP_{parsed_hard_time.strftime('%H%M')}"

    # Resolve quotes map
    quotes_map: Dict[str, float] = {}
    if isinstance(current_quotes, dict):
        quotes_map = {str(k).upper(): float(v) for k, v in current_quotes.items() if v is not None}

    exit_signals: List[ZeroDteExitSignal] = []

    for pos in pos_list:
        if isinstance(pos, dict):
            symbol = str(pos.get("contract_symbol", pos.get("symbol", "")))
            underlying = str(pos.get("symbol", symbol.split()[0])).upper()
            qty = float(pos.get("quantity", pos.get("qty", 1.0)))
            entry_price = float(pos.get("entry_price", pos.get("avg_entry_price", pos.get("avg_cost", 0.0))))
            pos_id = str(pos.get("position_id", pos.get("id", f"pos_{uuid.uuid4().hex[:6]}")))
            market_value = pos.get("market_value")
        else:
            symbol = str(getattr(pos, "symbol", ""))
            underlying = symbol.split()[0].upper()
            qty = float(getattr(pos, "qty", 0.0))
            entry_price = float(getattr(pos, "avg_entry_price", 0.0))
            pos_id = f"pos_{symbol}_{uuid.uuid4().hex[:6]}"
            market_value = getattr(pos, "market_value", None)

        if abs(qty) < _DEGENERATE_THRESHOLD:
            continue

        abs_qty = abs(qty)

        # Current price resolution
        if symbol.upper() in quotes_map:
            current_mark = quotes_map[symbol.upper()]
        elif underlying in quotes_map:
            current_mark = quotes_map[underlying]
        elif isinstance(pos, dict) and "current_price" in pos and float(pos["current_price"]) > 0:
            current_mark = float(pos["current_price"])
        elif market_value is not None and abs_qty > 0:
            current_mark = abs(float(market_value)) / (abs_qty * 100.0) if abs(float(market_value)) > 10.0 else abs(float(market_value)) / abs_qty
        else:
            current_mark = entry_price

        # Standardize entry price & calculate P&L %
        if qty > 0:
            closing_side = "sell"
            if entry_price > 0:
                pnl_pct = (current_mark - entry_price) / entry_price
            else:
                pnl_pct = 0.0
            unrealized_pl = (current_mark - entry_price) * abs_qty * (100.0 if entry_price < 50.0 else 1.0)
        else:
            closing_side = "buy"
            if entry_price > 0:
                pnl_pct = (entry_price - current_mark) / entry_price
            else:
                pnl_pct = 0.0
            unrealized_pl = (entry_price - current_mark) * abs_qty * (100.0 if entry_price < 50.0 else 1.0)

        exit_type: Optional[str] = None
        exit_reason: Optional[str] = None
        urgent = False
        trigger = ""
        reason_detail = ""

        if is_hard_stop_active:
            exit_type = "EXIT_HARD_TIME_STOP"
            exit_reason = hard_stop_reason_code
            urgent = True
            trigger = "HARD_TIME_STOP"
            reason_detail = f"Hard Time Stop reached ({parsed_current_time.strftime('%H:%M')} >= {parsed_hard_time.strftime('%H:%M')} ET); closing position to eliminate pin/settlement risk."
        elif pnl_pct >= profit_target_pct:
            exit_type = "EXIT_PROFIT_TARGET"
            exit_reason = f"PROFIT_TARGET_{int(profit_target_pct * 100)}"
            urgent = False
            trigger = "PROFIT_TARGET"
            reason_detail = f"Profit target triggered: P&L +{pnl_pct:.1%} >= +{profit_target_pct:.1%}"
        elif pnl_pct <= -stop_loss_pct:
            exit_type = "EXIT_STOP_LOSS"
            exit_reason = f"STOP_LOSS_{int(stop_loss_pct * 100)}"
            urgent = True
            trigger = "STOP_LOSS"
            reason_detail = f"Stop loss triggered: P&L {pnl_pct:.1%} <= -{stop_loss_pct:.1%}"

        if exit_type and exit_reason:
            signal = ZeroDteExitSignal(
                exit_type=exit_type,
                exit_reason=exit_reason,
                symbol=underlying,
                contract_symbol=symbol,
                position_id=pos_id,
                entry_price=round(entry_price, 4),
                current_price=round(current_mark, 4),
                pnl_pct=round(pnl_pct, 4),
                unrealized_pl=round(unrealized_pl, 2),
                quantity=abs_qty,
                contracts=max(1, int(round(abs_qty))),
                urgent=urgent,
                side=closing_side,
                action="CLOSE",
                trigger=trigger,
                reason_detail=reason_detail,
            )
            exit_signals.append(signal)

    return exit_signals


def execute_0dte_trade(
    symbol: str,
    side: str = "buy",
    strike: float = 0.0,
    expiration: Optional[str] = None,
    contracts: int = 1,
    store: Optional[PaperAccountStore] = None,
    *,
    option_type: Optional[str] = None,
    opt_type: Optional[str] = None,
    quote_price: Optional[float] = None,
    limit_price: Optional[float] = None,
    strategy_name: str = "0DTE Momentum Breakout",
    stop_loss_pct: Optional[float] = None,
    profit_target_pct: Optional[float] = None,
    dry_run: bool = False,
    is_live: bool = False,
) -> Dict[str, Any]:
    """
    Submits a 0DTE single-leg option order with strategy_name="0DTE Momentum Breakout"
    and updates the PaperAccountStore atomically.
    """
    if is_live:
        return {
            "ok": False,
            "message": "Advisory-Only Mode: Live 0DTE order execution is disabled. Please use paper mode.",
        }

    if store is None:
        store = PaperAccountStore()

    ticker = symbol.strip().upper()
    contracts = max(1, int(contracts))
    strike = float(strike)
    exp_date = expiration or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Determine action and option type
    side_lower = str(side).lower().strip()
    if side_lower in ("call", "puts", "put", "calls"):
        action = "buy"
        resolved_opt_type = "CALL" if "call" in side_lower else "PUT"
    elif side_lower in ("buy", "sell"):
        action = side_lower
        resolved_opt_type = str(option_type or opt_type or "CALL").upper().strip()
    else:
        action = "buy"
        resolved_opt_type = str(option_type or opt_type or "CALL").upper().strip()

    option_symbol = f"{ticker} {exp_date} ${strike:.2f} {resolved_opt_type}"
    client_order_id = f"0dte_{uuid.uuid4().hex[:10]}"

    # Price resolution ($/share)
    if quote_price is not None and quote_price > 0:
        unit_price = float(quote_price)
    elif limit_price is not None and limit_price > 0:
        unit_price = float(limit_price)
    else:
        unit_price = 1.50

    fill_price_contract = unit_price * _DEFAULT_MULTIPLIER
    commission = 0.65 * contracts

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "order_id": client_order_id,
            "symbol": ticker,
            "underlying": ticker,
            "contract_symbol": option_symbol,
            "strategy_name": strategy_name,
            "option_type": resolved_opt_type,
            "strike": strike,
            "expiration": exp_date,
            "contracts": contracts,
            "unit_price": unit_price,
            "fill_price": fill_price_contract,
            "profit_target_pct": profit_target_pct or 0.75,
            "stop_loss_pct": stop_loss_pct or 0.30,
            "message": f"Dry run: 0DTE {resolved_opt_type} order validated for {option_symbol}.",
        }

    if action == "buy":
        total_cost = (contracts * fill_price_contract) + commission
        net_cash_impact = -total_cost
        collateral_required = total_cost
    else:
        total_proceeds = (contracts * fill_price_contract) - commission
        net_cash_impact = total_proceeds
        collateral_required = strike * _DEFAULT_MULTIPLIER * contracts

    leg = {
        "symbol": option_symbol,
        "side": action,
        "qty": float(contracts),
        "fill_price": fill_price_contract,
        "strike": strike,
        "type": resolved_opt_type,
        "expiration": exp_date,
        "unit_price": unit_price,
    }

    success = store.apply_multi_leg_fill(
        client_order_id=client_order_id,
        symbol=ticker,
        strategy_name=strategy_name,
        contracts=contracts,
        legs=[leg],
        net_cash_impact=net_cash_impact,
        commission_and_fees=commission,
        collateral_required=collateral_required,
    )

    if not success:
        return {
            "ok": False,
            "order_id": client_order_id,
            "symbol": ticker,
            "underlying": ticker,
            "contract_symbol": option_symbol,
            "strategy_name": strategy_name,
            "contracts": contracts,
            "unit_price": unit_price,
            "message": f"Order rejected: Insufficient funds or collateral for {action.upper()} {contracts} 0DTE {resolved_opt_type} on {ticker}.",
        }

    return {
        "ok": True,
        "order_id": client_order_id,
        "symbol": ticker,
        "underlying": ticker,
        "contract_symbol": option_symbol,
        "strategy_name": strategy_name,
        "contracts": contracts,
        "unit_price": unit_price,
        "fill_price": fill_price_contract,
        "action": action,
        "net_cash_impact": net_cash_impact,
        "commission": commission,
        "profit_target_pct": profit_target_pct or 0.75,
        "stop_loss_pct": stop_loss_pct or 0.30,
        "hard_exit_time": getattr(settings, "OPTIONS_0DTE_HARD_EXIT_TIME", "15:45"),
        "message": f"Filled {strategy_name}: {action.upper()} {contracts} contract(s) of {option_symbol} at ${unit_price:.2f}/sh (Net: ${net_cash_impact:.2f}).",
    }



def execute_0dte_exits(
    exit_directives: Sequence[Any],
    store: Optional[PaperAccountStore] = None,
) -> Dict[str, Any]:
    """
    Executes closing fills for a sequence of 0DTE exit directives.
    """
    if store is None:
        store = PaperAccountStore()

    executed = []
    failed = []

    for item in exit_directives:
        if isinstance(item, dict):
            pos_symbol = item.get("contract_symbol", item.get("position_symbol", item.get("symbol", "")))
            side = item.get("side", "sell")
            qty = float(item.get("qty", item.get("quantity", item.get("contracts", 1))))
            price = float(item.get("current_price", item.get("fill_price", 1.0)))
            exit_reason = item.get("exit_reason", item.get("exit_type", "EXIT"))
        else:
            pos_symbol = getattr(item, "contract_symbol", getattr(item, "symbol", ""))
            side = getattr(item, "side", "sell")
            qty = float(getattr(item, "quantity", getattr(item, "qty", 1.0)))
            price = float(getattr(item, "current_price", 1.0))
            exit_reason = getattr(item, "exit_reason", "EXIT")

        client_order_id = f"exit_0dte_{uuid.uuid4().hex[:10]}"
        commission = 0.65 * max(1, int(round(qty)))
        fill_price_contract = price * _DEFAULT_MULTIPLIER

        opt_info = parse_option_symbol(pos_symbol)
        opt_type = opt_info["option_type"] if opt_info else "CALL"
        strike = opt_info["strike"] if opt_info else 0.0
        exp = opt_info["expiration"] if opt_info else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ticker = opt_info["ticker"] if opt_info else pos_symbol.split()[0]

        leg = {
            "symbol": pos_symbol,
            "side": side,
            "qty": qty,
            "fill_price": fill_price_contract,
            "strike": strike,
            "type": opt_type,
            "expiration": exp,
            "unit_price": price,
        }

        if side == "sell":
            net_cash_impact = (qty * fill_price_contract) - commission
        else:
            net_cash_impact = -((qty * fill_price_contract) + commission)

        success = store.apply_multi_leg_fill(
            client_order_id=client_order_id,
            symbol=ticker,
            strategy_name=f"Close 0DTE ({exit_reason})",
            contracts=max(1, int(round(qty))),
            legs=[leg],
            net_cash_impact=net_cash_impact,
            commission_and_fees=commission,
        )

        if success:
            executed.append({
                "order_id": client_order_id,
                "position_symbol": pos_symbol,
                "exit_reason": exit_reason,
                "net_cash_impact": net_cash_impact,
            })
        else:
            failed.append({
                "order_id": client_order_id,
                "position_symbol": pos_symbol,
                "exit_reason": exit_reason,
            })

    return {
        "executed_count": len(executed),
        "failed_count": len(failed),
        "executed": executed,
        "failed": failed,
    }

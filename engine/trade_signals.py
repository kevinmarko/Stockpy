# =============================================================================
# MODULE: TRADE SIGNALS  (two advisory trading abilities — ADVISORY ONLY)
# File: engine/trade_signals.py
#
# ADVISORY ONLY — this module derives two *actionable* trading abilities from
# data the advisory agent already has on hand each cycle (the `RunResult`'s
# recommendations + Robinhood account snapshot).  It contains NO order code of
# any kind; every output is a `TradeAlert` that the loop driver pushes through
# the existing `alerting.notify()` ntfy channel.
#
# Ability A — CONVICTION MOMENTUM
# -------------------------------
# The autonomous agent uniquely holds cross-cycle state.  The static backlog
# (engine/advisory_agent.py) only fires once conviction crosses the 0.85 siren.
# This ability watches each symbol's conviction *trajectory* and surfaces:
#   * "building"  — conviction climbing steadily but not yet at the siren, so
#                   the operator gets an EARLY heads-up before the move matures.
#   * "fading"    — conviction deteriorating on a name whose thesis is weakening
#                   (action no longer BUY), an EARLY exit warning.
# Both are edge-triggered (debounced per symbol) so a sustained trend pings once,
# not every cycle.
#
# Ability B — STOP / TARGET PROXIMITY
# -----------------------------------
# For HELD positions, derives a volatility-scaled (ATR) stop below cost basis
# and a take-profit target from the 30-day forecast, then alerts when the live
# price approaches (or breaches) either level.  Turns the agent from an entry
# nagger into a position-management assistant.
#
# No-lookahead / purity invariant
# -------------------------------
# Every `update_*` / `detect_*` function is pure: it consumes only the current
# cycle's recommendations, the account snapshot, the previous debounce state,
# and `now_utc`.  It never calls a market-data provider, forecasting engine, or
# any source of future-dated data.  Verified by the Gravity audit.
# =============================================================================

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Policy CONFIG — single source of truth for every threshold.
# No magic numbers belong in the logic functions below.
# ---------------------------------------------------------------------------
CONFIG: Dict[str, Any] = {
    # ── Ability A: conviction momentum ─────────────────────────────────
    # Rolling per-symbol conviction window length (cycles retained).
    "momentum_lookback_cycles": 5,
    # Minimum number of stored convictions before a trend is judged.
    "momentum_min_cycles": 3,
    # Total conviction rise across the window required to flag "building".
    "momentum_rising_delta": 0.10,
    # Only flag "building" once conviction is at least this (avoid noise on
    # low-conviction names) AND strictly below the backlog siren (avoid
    # double-alerting with engine/advisory_agent's backlog).
    "momentum_building_floor": 0.60,
    "momentum_building_ceiling": 0.85,
    # Total conviction drop across the window required to flag "fading".
    "momentum_falling_delta": 0.15,

    # ── Ability B: stop / target proximity ─────────────────────────────
    # Volatility-scaled stop distance below average cost, in ATR multiples.
    "stop_atr_multiple": 2.5,
    # Fallback stop distance (fraction below cost) when ATR is unavailable.
    "stop_fallback_pct": 0.08,
    # Price within this fraction ABOVE the stop (or already below it) → alert.
    "stop_proximity_pct": 0.02,
    # ATR multiples above cost for a take-profit target when no usable forecast.
    "target_atr_multiple": 3.0,
    # Price within this fraction BELOW the target (or already above it) → alert.
    "target_proximity_pct": 0.02,
    # Ignore dust positions below this market value (USD).
    "min_position_value_usd": 100.0,
}


# ---------------------------------------------------------------------------
# Alert dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TradeAlert:
    """One actionable trading alert ready for `alerting.notify()`.

    Pure data object; `dispatch_trade_alerts()` consumes a list of these and
    translates them into ntfy calls.

    Attributes
    ----------
    symbol :
        Uppercase ticker.
    kind :
        One of ``"momentum_building"``, ``"momentum_fading"``,
        ``"approaching_stop"``, ``"approaching_target"``.
    priority :
        ntfy priority — ``"default"`` | ``"high"``.
    title / message :
        Operator-facing strings.
    detail :
        Free-form numeric context (conviction trajectory, stop level, …) for
        logging / tests — never fabricated, NaN where unavailable.
    """
    symbol: str
    kind: str
    priority: str
    title: str
    message: str
    detail: Dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Small numeric helpers
# ---------------------------------------------------------------------------

def _finite(x: Any) -> bool:
    """True iff `x` coerces to a finite float."""
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _f(x: Any, default: float = float("nan")) -> float:
    """Coerce to float; return `default` on failure."""
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Ability A — conviction momentum
# ---------------------------------------------------------------------------

def update_conviction_history(
    history: Dict[str, List[float]],
    recommendations: List[Any],
    *,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[float]]:
    """Append this cycle's conviction per symbol and trim to the lookback window.

    Pure: returns a NEW dict; the input is not mutated.  Symbols absent from
    the current cycle's recommendations are dropped, so the history tracks the
    live universe and cannot grow without bound.

    Parameters
    ----------
    history :
        Previous ``{symbol: [conviction, …]}`` map (oldest first).
    recommendations :
        Iterable of `Recommendation`-shaped objects exposing ``.symbol`` and
        ``.conviction`` (duck-typed).
    """
    cfg = {**CONFIG, **(config or {})}
    lookback = max(1, int(cfg["momentum_lookback_cycles"]))

    new_history: Dict[str, List[float]] = {}
    for rec in recommendations or []:
        try:
            symbol = str(getattr(rec, "symbol", "")).upper()
            conv = getattr(rec, "conviction", None)
        except Exception:
            continue
        if not symbol or not _finite(conv):
            continue
        prior = list(history.get(symbol, []))
        prior.append(round(float(conv), 6))
        new_history[symbol] = prior[-lookback:]
    return new_history


def detect_conviction_momentum(
    history: Dict[str, List[float]],
    recommendations: List[Any],
    alerted: Dict[str, str],
    *,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[List[TradeAlert], Dict[str, str]]:
    """Surface symbols whose conviction is steadily building or fading.

    Edge-triggered: a sustained trend alerts ONCE.  `alerted` maps
    ``symbol -> last-direction`` (``"building"`` | ``"fading"`` | ``""``); a
    symbol re-alerts only when its direction flips or its trend resets.

    Returns ``(alerts, new_alerted)``.  Does not mutate its inputs.

    `history` should be the POST-append map from `update_conviction_history`.
    """
    cfg = {**CONFIG, **(config or {})}
    min_cycles = max(2, int(cfg["momentum_min_cycles"]))
    rising_delta = float(cfg["momentum_rising_delta"])
    falling_delta = float(cfg["momentum_falling_delta"])
    floor = float(cfg["momentum_building_floor"])
    ceiling = float(cfg["momentum_building_ceiling"])

    actions: Dict[str, str] = {}
    for rec in recommendations or []:
        try:
            sym = str(getattr(rec, "symbol", "")).upper()
            actions[sym] = str(getattr(rec, "action", "")).upper()
        except Exception:
            continue

    out: List[TradeAlert] = []
    new_alerted: Dict[str, str] = {}

    for symbol, series in history.items():
        if len(series) < min_cycles:
            # Carry forward any prior debounce flag until we have enough data.
            if alerted.get(symbol):
                new_alerted[symbol] = alerted[symbol]
            continue

        window = series[-min_cycles:]
        first, last = window[0], window[-1]
        rise = last - first
        drop = first - last
        non_decreasing = all(b >= a for a, b in zip(window, window[1:]))
        non_increasing = all(b <= a for a, b in zip(window, window[1:]))
        action = actions.get(symbol, "")

        direction = ""
        # Building: a steady climb that has not yet reached the backlog siren.
        if (
            non_decreasing
            and rise >= rising_delta
            and floor <= last < ceiling
            and "SELL" not in action
        ):
            direction = "building"
        # Fading: a steady decline on a name no longer flagged BUY.
        elif (
            non_increasing
            and drop >= falling_delta
            and "BUY" not in action
        ):
            direction = "fading"

        if not direction:
            # Trend reset → clear debounce so a future move can re-alert.
            continue

        if alerted.get(symbol) == direction:
            # Already alerted this exact direction; stay quiet but remember it.
            new_alerted[symbol] = direction
            continue

        new_alerted[symbol] = direction
        if direction == "building":
            out.append(TradeAlert(
                symbol=symbol,
                kind="momentum_building",
                priority="default",
                title=f"📈 {symbol} conviction building ({last:.2f})",
                message=(
                    f"{symbol} conviction has climbed {rise:+.2f} over the last "
                    f"{len(window)} cycles to {last:.2f} (action {action or 'n/a'}). "
                    f"Building toward an entry — watch for confirmation."
                ),
                detail={"first": first, "last": last, "rise": rise},
            ))
        else:
            out.append(TradeAlert(
                symbol=symbol,
                kind="momentum_fading",
                priority="high",
                title=f"📉 {symbol} conviction fading ({last:.2f})",
                message=(
                    f"{symbol} conviction has fallen {drop:+.2f} over the last "
                    f"{len(window)} cycles to {last:.2f} (action {action or 'n/a'}). "
                    f"Thesis weakening — review the position."
                ),
                detail={"first": first, "last": last, "drop": drop},
            ))

    return out, new_alerted


# ---------------------------------------------------------------------------
# Ability B — stop / target proximity
# ---------------------------------------------------------------------------

def detect_price_triggers(
    snapshot: Any,
    recommendations: List[Any],
    alerted: Dict[str, str],
    *,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[List[TradeAlert], Dict[str, str]]:
    """Alert when a held position approaches its stop or take-profit target.

    For each position with quantity > 0 and market value ≥
    ``min_position_value_usd``:

      * STOP  — a volatility-scaled level ``average_cost − stop_atr_multiple*ATR``
        (fallback ``average_cost*(1 − stop_fallback_pct)`` when ATR is missing).
        Fires HIGH when ``price ≤ stop*(1 + stop_proximity_pct)`` — i.e. within
        the proximity band above the stop, or already breached.
      * TARGET — the 30-day forecast price when it is above current price
        (fallback ``average_cost + target_atr_multiple*ATR``).  Fires DEFAULT
        when ``price ≥ target*(1 − target_proximity_pct)`` — at/near the target.

    Edge-triggered per symbol via `alerted` (``"stop"`` | ``"target"`` | ``""``);
    a position re-alerts only when its trigger changes.  Returns
    ``(alerts, new_alerted)``; inputs are not mutated.

    Stop is checked before target so a position straddling both (unlikely)
    surfaces the risk side.  No fabricated levels — a position with neither a
    usable ATR nor a usable forecast simply yields no target alert.
    """
    cfg = {**CONFIG, **(config or {})}
    stop_mult = float(cfg["stop_atr_multiple"])
    stop_fallback = float(cfg["stop_fallback_pct"])
    stop_prox = float(cfg["stop_proximity_pct"])
    target_mult = float(cfg["target_atr_multiple"])
    target_prox = float(cfg["target_proximity_pct"])
    min_value = float(cfg["min_position_value_usd"])

    positions = getattr(snapshot, "positions", None) or {}
    if not isinstance(positions, dict):
        return [], {}

    # Index recommendations by symbol for ATR / forecast lookup.
    rec_by_symbol: Dict[str, Any] = {}
    for rec in recommendations or []:
        try:
            rec_by_symbol[str(getattr(rec, "symbol", "")).upper()] = rec
        except Exception:
            continue

    out: List[TradeAlert] = []
    new_alerted: Dict[str, str] = {}

    for raw_sym, pos in positions.items():
        try:
            symbol = str(getattr(pos, "symbol", raw_sym)).upper()
            qty = _f(getattr(pos, "quantity", 0.0), 0.0)
            price = _f(getattr(pos, "current_price", 0.0), 0.0)
            avg_cost = _f(getattr(pos, "average_cost", 0.0), 0.0)
            mkt_value = _f(getattr(pos, "market_value", 0.0), 0.0)
        except Exception:
            continue
        if qty <= 0 or price <= 0 or avg_cost <= 0 or mkt_value < min_value:
            continue

        rec = rec_by_symbol.get(symbol)
        atr = _f(getattr(rec, "key_indicators", {}).get("atr"), float("nan")) if rec else float("nan")
        forecast = _f(getattr(rec, "forecast", None), float("nan")) if rec else float("nan")

        # ── Stop level (volatility-scaled, anchored on cost basis) ───────
        if _finite(atr) and atr > 0:
            stop_level = avg_cost - stop_mult * atr
        else:
            stop_level = avg_cost * (1.0 - stop_fallback)
        stop_level = max(stop_level, 0.01)

        # ── Target level (forecast first, ATR fallback) ──────────────────
        # Only a BULLISH forecast (above current price) is a meaningful
        # take-profit target for a long position.  A bearish forecast (below
        # price) must NOT be used as a target — it would make the proximity
        # test fire immediately since price >= bearish_target*(1-prox) is
        # trivially true, alerting "near target" on every position the model
        # expects to decline.  Fall through to the ATR-based target instead.
        if _finite(forecast) and forecast > price:
            target_level: float = forecast
        elif _finite(atr) and atr > 0:
            target_level = avg_cost + target_mult * atr
        else:
            target_level = float("nan")

        trigger = ""
        if price <= stop_level * (1.0 + stop_prox):
            trigger = "stop"
        elif _finite(target_level) and price >= target_level * (1.0 - target_prox):
            trigger = "target"

        if not trigger:
            continue  # no active trigger → debounce flag cleared by omission

        if alerted.get(symbol) == trigger:
            new_alerted[symbol] = trigger  # already alerted; stay quiet
            continue

        new_alerted[symbol] = trigger
        pl_pct = _f(getattr(pos, "unrealized_pl_pct", float("nan")), float("nan"))
        if trigger == "stop":
            breached = price < stop_level
            verb = "breached" if breached else "approaching"
            out.append(TradeAlert(
                symbol=symbol,
                kind="approaching_stop",
                priority="high",
                title=f"🛑 {symbol} {verb} stop (${price:.2f} vs ${stop_level:.2f})",
                message=(
                    f"{symbol} is {verb} its ATR stop: price ${price:.2f}, "
                    f"stop ${stop_level:.2f}, cost ${avg_cost:.2f}, "
                    f"unrealized {pl_pct:+.1f}%. Review for a protective exit."
                ),
                detail={"price": price, "stop_level": stop_level,
                        "avg_cost": avg_cost, "atr": atr, "pl_pct": pl_pct},
            ))
        else:
            out.append(TradeAlert(
                symbol=symbol,
                kind="approaching_target",
                priority="default",
                title=f"🎯 {symbol} near target (${price:.2f} vs ${target_level:.2f})",
                message=(
                    f"{symbol} has reached its take-profit zone: price ${price:.2f}, "
                    f"target ${target_level:.2f}, cost ${avg_cost:.2f}, "
                    f"unrealized {pl_pct:+.1f}%. Consider trimming or tightening a stop."
                ),
                detail={"price": price, "target_level": target_level,
                        "avg_cost": avg_cost, "forecast": forecast, "pl_pct": pl_pct},
            ))

    return out, new_alerted


# ---------------------------------------------------------------------------
# Dispatch helper (kept thin — mirrors advisory_agent.dispatch_backlog_reminders)
# ---------------------------------------------------------------------------

def dispatch_trade_alerts(
    alerts: List[TradeAlert],
    *,
    dashboard_url: Optional[str] = None,
) -> None:
    """Push every trade alert via `alerting.notify()`.

    When `NTFY_TOPIC` is unset `notify()` is already a no-op, so this is
    silently inert with no ntfy configuration.  Per-alert dispatch is wrapped
    in try/except — a network failure on one alert never blocks the rest
    (CONSTRAINT #6).
    """
    if not alerts:
        return
    from alerting import notify  # noqa: PLC0415 — match watch_engine/advisory_agent pattern
    for a in alerts:
        try:
            msg = a.message
            if dashboard_url:
                msg = f"{msg}\n\n📊 Dashboard: {dashboard_url}"
            notify(title=a.title, message=msg, priority=a.priority)
            logger.info(
                "Trade alert dispatched — symbol=%s kind=%s priority=%s",
                a.symbol, a.kind, a.priority,
            )
        except Exception as exc:
            logger.warning(
                "Failed to dispatch trade alert for %s (%s): %s",
                a.symbol, a.kind, exc,
            )

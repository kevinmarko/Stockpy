# =============================================================================
# MODULE: EXECUTION QUEUE BUILDER  (Tier 8 — Robinhood execution bridge)
# File: execution/queue_builder.py
#
# This module lives INSIDE the sanctioned `execution/` order-code zone.  It is
# the seam between the headless advisory pipeline and the Robinhood Trading MCP:
# the pipeline cannot call MCP tools (those are LLM-agent tools), so instead it
# emits a GATED, DRY-RUN list of proposed order INTENTS to
# `output/execution_queue.json`.  A separate Claude Code agent reads that file
# and is the only actor that ever calls the MCP `review_equity_order` /
# `place_equity_order` tools.
#
# This module NEVER contacts a broker and NEVER places an order.  It only:
#   1. Translates actionable advisory Recommendations into `OrderIntent`s.
#   2. Runs them through the existing `PreTradeRiskGate` + `GlobalKillSwitch`
#      (the same decision stack the Alpaca path uses), in dry-run.
#   3. Writes the gated queue to disk — but ONLY when the execution mode is
#      `review` or `live`.  In the default `off` mode nothing is written.
#
# Safety invariant: `allow_place` is computed here as
#       mode == "live"  AND  gate passed  AND  kill switch clear
#       AND  a per-order notional cap is configured.
# It is therefore STRUCTURALLY False in every non-live posture.  The downstream
# agent treats `allow_place=false` as "preview only".
#
# Function names here deliberately avoid the order-submission tokens the
# repo-wide AST guard (`tests/test_pipeline_smoke.py::TestNoOrderFunctions`)
# forbids — there is no `place_*` / `submit_order` / `*_order` definition.
# =============================================================================

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from execution.broker_base import (
    AccountSnapshot as BrokerAccountSnapshot,
    OrderIntent,
    OrderSide,
    OrderType,
    PositionSnapshot,
)
from execution.kill_switch import GlobalKillSwitch
from execution.order_manager import make_client_order_id
from execution.risk_gate import PreTradeRiskGate, RiskContext

logger = logging.getLogger(__name__)

VALID_MODES = ("off", "review", "live")

# Single source of truth for builder thresholds (no magic numbers below).
CONFIG: Dict[str, Any] = {
    # A recommendation becomes a queued intent only at/above this conviction.
    # Mirrors the autonomous backlog siren (engine/advisory_agent.py).
    "min_conviction": 0.85,
    # strategy_id stamped on every intent (drives the deterministic order id).
    "strategy_id": "advisory",
}

_QUEUE_FILENAME = "execution_queue.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _f(x: Any, default: float = 0.0) -> float:
    """Coerce to a finite float; return ``default`` on failure / NaN / inf."""
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _resolve_mode(mode: Optional[str]) -> str:
    """Return a validated execution mode, falling back to settings, then ``off``."""
    if mode is None:
        try:
            from settings import settings  # local import — avoid import cycle
            mode = getattr(settings, "ROBINHOOD_EXECUTION_MODE", "off")
        except Exception:
            mode = "off"
    m = str(mode or "").strip().lower()
    return m if m in VALID_MODES else "off"


def _max_notional() -> float:
    try:
        from settings import settings
        return max(0.0, _f(getattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 0.0)))
    except Exception:
        return 0.0


def _build_risk_context(snapshot: Any, now: datetime) -> RiskContext:
    """Construct a best-effort `RiskContext` from the Robinhood account snapshot.

    Missing data leaves the corresponding checks to conservative-pass (the gate's
    own documented behaviour) — this is a PRE-SCREEN.  Robinhood's own
    `review_equity_order` pre-trade warnings plus per-trade human confirmation
    are the authoritative checks downstream.
    """
    positions: List[PositionSnapshot] = []
    current_prices: Dict[str, float] = {}
    equity = _f(getattr(snapshot, "total_equity", 0.0))
    buying_power = _f(getattr(snapshot, "buying_power", 0.0))

    raw_positions = getattr(snapshot, "positions", {}) or {}
    if isinstance(raw_positions, dict):
        iterable = raw_positions.values()
    else:
        iterable = raw_positions
    for pos in iterable:
        try:
            sym = str(getattr(pos, "symbol", "")).upper()
            if not sym:
                continue
            qty = _f(getattr(pos, "quantity", 0.0))
            price = _f(getattr(pos, "current_price", 0.0))
            positions.append(PositionSnapshot(
                symbol=sym,
                qty=qty,
                avg_entry_price=_f(getattr(pos, "average_cost", 0.0)),
                market_value=_f(getattr(pos, "market_value", 0.0)),
                unrealized_pl=_f(getattr(pos, "unrealized_pl", 0.0)),
            ))
            if price > 0:
                current_prices[sym] = price
        except Exception as exc:
            logger.debug("queue_builder: skipping position in risk context (%s)", exc)

    account = BrokerAccountSnapshot(equity=equity, cash=buying_power, buying_power=buying_power)
    return RiskContext(
        macro=None,
        open_positions=positions,
        account=account,
        returns_df=None,
        start_of_day_equity=equity or None,
        is_premium_sell_strategy=False,
        current_prices=current_prices,
        timestamp=now,
    )


def gate_intent(
    intent: OrderIntent,
    context: RiskContext,
    gate: Optional[PreTradeRiskGate] = None,
) -> Tuple[bool, List[str]]:
    """Run the pre-trade risk gate for one intent.

    Returns ``(allowed, reasons)``.  ``reasons`` lists the failing checks when
    blocked, or is empty when allowed.  Any exception fails CLOSED — an intent
    whose gate could not be evaluated is never marked allowed (returns
    ``(False, ["gate_error: ..."])``).
    """
    gate = gate or PreTradeRiskGate()
    try:
        allowed, results = gate.run_all(intent, context)
        reasons = [f"{r.check_name}: {r.reason}" for r in results if not r.passed]
        return bool(allowed), reasons
    except Exception as exc:
        logger.warning("queue_builder: risk gate raised for %s (%s); failing closed",
                       getattr(intent, "symbol", "?"), exc)
        return False, [f"gate_error: {exc}"]


def _intent_dict(
    rec: Any,
    snapshot: Any,
    *,
    mode: str,
    kill_switch_active: bool,
    max_notional: float,
    gate: PreTradeRiskGate,
    context: RiskContext,
    now: datetime,
    min_conviction: float,
    strategy_id: str,
) -> Optional[Dict[str, Any]]:
    """Translate one Recommendation into a gated queue-intent dict, or ``None``.

    BUY  → ``qty`` is left null and ``target_notional`` (capped) is emitted; the
           execution agent computes the share count from a LIVE MCP quote at
           review time (the headless pipeline has no live price for unheld names).
    SELL → only for HELD symbols; ``qty`` is the held quantity (a full exit),
           ``target_notional`` is the held market value.  A SELL of an unheld
           symbol is dropped (nothing to sell — no fabricated position).
    """
    action = str(getattr(rec, "action", "")).upper()
    symbol = str(getattr(rec, "symbol", "")).upper()
    conviction = _f(getattr(rec, "conviction", 0.0))
    if not symbol or action not in ("BUY", "SELL") or conviction < min_conviction:
        return None

    held = None
    raw_positions = getattr(snapshot, "positions", {}) or {}
    if isinstance(raw_positions, dict):
        held = raw_positions.get(symbol)

    side = OrderSide.BUY if action == "BUY" else OrderSide.SELL
    qty: Optional[float] = None
    target_notional: Optional[float] = None
    gate_qty = 1.0  # provisional, for the gate object only

    if action == "BUY":
        equity = _f(getattr(snapshot, "total_equity", 0.0))
        pct = _f(getattr(rec, "suggested_position_pct", 0.0))  # fraction, e.g. 0.05
        notional = equity * pct
        if max_notional > 0:
            notional = min(notional, max_notional)
        if notional <= 0:
            return None
        target_notional = round(notional, 2)
        # Provisional qty for gating only, when we happen to hold the name.
        if held is not None:
            price = _f(getattr(held, "current_price", 0.0))
            if price > 0:
                gate_qty = max(1.0, math.floor(notional / price))
    else:  # SELL
        if held is None:
            return None  # cannot sell what is not held
        held_qty = _f(getattr(held, "quantity", 0.0))
        if held_qty <= 0:
            return None
        qty = held_qty
        gate_qty = held_qty
        target_notional = round(_f(getattr(held, "market_value", 0.0)), 2)

    client_order_id = make_client_order_id(
        strategy_id, symbol, side.value, gate_qty, timestamp=now,
    )
    intent = OrderIntent(
        strategy_id=strategy_id,
        symbol=symbol,
        side=side,
        qty=gate_qty,
        order_type=OrderType.MARKET,
        client_order_id=client_order_id,
        dry_run=True,
    )

    gate_allowed, gate_reasons = gate_intent(intent, context, gate)

    notional_cap_ok = max_notional > 0  # live REQUIRES a configured cap
    allow_place = bool(
        mode == "live"
        and gate_allowed
        and not kill_switch_active
        and notional_cap_ok
    )
    if mode == "live" and not notional_cap_ok and "notional_cap_unset" not in gate_reasons:
        gate_reasons = gate_reasons + ["notional_cap_unset"]

    return {
        "client_order_id": client_order_id,
        "symbol": symbol,
        "action": action,
        "side": side.value,
        "qty": qty,
        "target_notional": target_notional,
        "order_type": OrderType.MARKET.value,
        "limit_price": None,
        "conviction": round(conviction, 4),
        "gate_allowed": gate_allowed,
        "gate_reasons": gate_reasons,
        "allow_place": allow_place,
        "rationale": str(getattr(rec, "strategy", "") or getattr(rec, "rationale", ""))[:240],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_execution_queue(
    run_result: Any,
    *,
    mode: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build the gated execution-queue payload from a `RunResult`.

    Pure with respect to the broker — runs the risk gate + kill-switch check but
    NEVER contacts a broker or the MCP.  Returns the full payload dict (callers
    that only want the file should use `emit_execution_queue`).  Per-symbol
    failures are logged and skipped (dead-letter resilient).
    """
    cfg = {**CONFIG, **(config or {})}
    resolved_mode = _resolve_mode(mode)
    now = now or datetime.now(timezone.utc)
    snapshot = getattr(run_result, "snapshot", None)
    recommendations = getattr(run_result, "recommendations", []) or []

    kill_switch_active = False
    try:
        kill_switch_active = GlobalKillSwitch().is_active()
    except Exception as exc:
        logger.debug("queue_builder: kill-switch check failed (%s); assuming inactive", exc)

    max_notional = _max_notional()
    gate = PreTradeRiskGate()
    context = _build_risk_context(snapshot, now)

    intents: List[Dict[str, Any]] = []
    for rec in recommendations:
        try:
            d = _intent_dict(
                rec, snapshot,
                mode=resolved_mode,
                kill_switch_active=kill_switch_active,
                max_notional=max_notional,
                gate=gate,
                context=context,
                now=now,
                min_conviction=float(cfg["min_conviction"]),
                strategy_id=str(cfg["strategy_id"]),
            )
            if d is not None:
                intents.append(d)
        except Exception as exc:
            logger.warning("queue_builder: failed to build intent for %s (%s)",
                           getattr(rec, "symbol", "?"), exc)

    return {
        "generated_at": now.isoformat(),
        "mode": resolved_mode,
        "kill_switch_active": kill_switch_active,
        "max_notional_per_order": max_notional,
        "n_intents": len(intents),
        "n_placeable": sum(1 for i in intents if i["allow_place"]),
        "intents": intents,
    }


def emit_execution_queue(
    run_result: Any,
    *,
    mode: Optional[str] = None,
    output_dir: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """Build and atomically write `output/execution_queue.json`.

    Returns the written `Path`, or ``None`` when the execution mode is ``off``
    (the default) — in which case NOTHING is written and there is zero
    behavioural change.  Never raises: a write failure is logged and swallowed
    (CONSTRAINT #6) so a best-effort caller in the advisory loop is never
    destabilised by this bridge.
    """
    resolved_mode = _resolve_mode(mode)
    if resolved_mode == "off":
        return None

    try:
        payload = build_execution_queue(run_result, mode=resolved_mode, config=config, now=now)
        if output_dir is None:
            from settings import settings
            output_dir = Path(settings.OUTPUT_DIR)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / _QUEUE_FILENAME
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
        logger.info(
            "Execution queue written (mode=%s, intents=%d, placeable=%d) → %s",
            resolved_mode, payload["n_intents"], payload["n_placeable"], path,
        )
        return path
    except Exception as exc:
        logger.warning("queue_builder: failed to emit execution queue (%s); skipping", exc)
        return None

"""Tier 8 gated, dry-run EQUITY execution-queue builder (Robinhood MCP bridge). Emits output/execution_queue.json for a Claude Code agent to consume, gating each OrderIntent through PreTradeRiskGate + GlobalKillSwitch in dry-run. Writes nothing in 'off' mode; allow_place is structurally False unless mode=='live' AND the gate passes AND the kill switch is clear AND a notional cap is set. Never contacts a broker."""

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
#
# Order type: MARKET by default.  When `settings.ROBINHOOD_LIMIT_BUFFER_BPS > 0`
# every intent becomes a LIMIT order carrying `order_type="limit"` +
# `limit_offset_bps` (the configured buffer); `limit_price` stays null and is
# resolved downstream from a live MCP quote at review time (see the verbatim
# LIMIT-PRICE CONTRACT block in `_intent_dict`).  A buffer of 0 is byte-identical
# to the legacy MARKET-only behaviour (no `limit_offset_bps` key emitted).
#
# Proactive notification: `emit_execution_queue` fires an `alerting.notify()`
# push (ntfy, no-op unless NTFY_TOPIC is set) whenever the written queue
# contains an intent the operator hasn't already been told about — so the
# operator can be pulled into the `robinhood-execution` skill from outside
# the chat window instead of having to remember to check. See
# `_notify_new_intents` below for the dedup rule.
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

# Max length of the per-intent ``rationale`` string in the emitted queue. Sized
# for the operator-facing "why" a reviewer reads before approving an order —
# NOT a terse label. A standard advisory rationale is ~120 chars, but
# RATIONALE_VERBOSITY=verbose (engine/advisory._build_rationale) appends four
# multi-part [A]/[B]/[C]/[D] sections that run 800-1500+ chars; 1200 keeps the
# common verbose case whole while bounding a pathological one. When a rationale
# does exceed this, ``_truncate_rationale`` cuts on a WORD boundary with an
# explicit "…" marker — never a silent mid-sentence chop (a silently truncated
# reason is worse than a short one).
_RATIONALE_MAX_CHARS = 1200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate_rationale(text: str, limit: int = _RATIONALE_MAX_CHARS) -> str:
    """Truncate ``text`` to at most ``limit`` chars on a WORD boundary, marking
    a real cut with a trailing "…". Never chops mid-word/mid-sentence silently.

    A cut always leaves visible evidence (the ellipsis) so a reader can tell the
    reason is incomplete rather than mistaking a truncated fragment for the whole
    thing (CONSTRAINT #4 in spirit — don't present a partial value as complete).
    """
    if len(text) <= limit:
        return text
    # Reserve one char for the ellipsis, then back up to the last whitespace so
    # we never split a word. Fall back to a hard slice if there's no space
    # (a single 1200-char token — pathological, but must still terminate).
    head = text[: limit - 1]
    cut = head.rfind(" ")
    if cut > 0:
        head = head[:cut]
    return head.rstrip() + "…"


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


def _limit_buffer_bps() -> int:
    """Return the configured limit-order buffer in basis points (>=0).

    0 (default) → MARKET orders, byte-identical to the legacy behaviour.
    A positive value → LIMIT orders with a ``limit_offset_bps`` field; the actual
    limit price is resolved DOWNSTREAM by the execution skill from a live quote
    (the headless pipeline has no live price). Never negative.
    """
    try:
        from settings import settings
        return max(0, int(_f(getattr(settings, "ROBINHOOD_LIMIT_BUFFER_BPS", 0))))
    except Exception:
        return 0


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
    limit_buffer_bps: int = 0,
) -> Optional[Dict[str, Any]]:
    """Translate one Recommendation into a gated queue-intent dict, or ``None``.

    BUY  → ``qty`` is left null and ``target_notional`` (capped) is emitted; the
           execution agent computes the share count from a LIVE MCP quote at
           review time (the headless pipeline has no live price for unheld names).
    SELL → only for HELD symbols.  A FULL exit (the default, when the rec carries
           no positive ``suggested_position_pct``) emits ``qty`` = the held
           quantity and ``target_notional`` = the held market value.  A PARTIAL
           TRIM (rec carries ``suggested_position_pct > 0`` — the Pilot-follow
           rebalance signal) instead leaves ``qty`` null and emits a notional-sized
           ``target_notional`` (``equity * pct``, capped by the per-order cap AND
           the held market value); the execution agent resolves the share count
           from a live quote like a BUY and caps it at the held quantity.  A SELL
           of an unheld symbol is dropped (nothing to sell — no fabricated position).
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
        held_mv = _f(getattr(held, "market_value", 0.0))
        # A SELL carrying a positive `suggested_position_pct` is a PARTIAL TRIM to a
        # target notional (a Pilot-follow rebalance): size it by notional exactly
        # like a BUY — `qty` stays null and is resolved DOWNSTREAM from a live quote
        # at review time, then capped at the held quantity so a trim can never
        # oversell. A SELL with pct == 0 (every advisory RISK-REDUCE exit, whose
        # exit fraction lives on a separate field and never lands in
        # suggested_position_pct) keeps the legacy FULL-exit semantics below,
        # byte-identical to before this branch existed.
        trim_pct = _f(getattr(rec, "suggested_position_pct", 0.0))
        if trim_pct > 0:
            equity = _f(getattr(snapshot, "total_equity", 0.0))
            notional = equity * trim_pct
            if max_notional > 0:
                notional = min(notional, max_notional)
            if held_mv > 0:
                notional = min(notional, held_mv)  # never trim more than we hold
            if notional <= 0:
                return None
            qty = None  # resolved downstream, capped at held_qty (see qty contract)
            target_notional = round(notional, 2)
            price = _f(getattr(held, "current_price", 0.0))
            gate_qty = held_qty
            if price > 0:
                gate_qty = max(1.0, min(held_qty, math.floor(notional / price)))
        else:
            qty = held_qty
            gate_qty = held_qty
            target_notional = round(held_mv, 2)

    # -----------------------------------------------------------------------
    # LIMIT-PRICE CONTRACT (verbatim — the robinhood-execution skill/docs copy
    # this rule as-is).  When `limit_buffer_bps > 0` every intent is a LIMIT
    # order carrying only the buffer; the ACTUAL limit price stays null here and
    # is resolved DOWNSTREAM from a LIVE MCP quote at review time (the headless
    # pipeline has no live price).  The price the skill computes MUST satisfy:
    #     BUY  limit <= quote * (1 + bps/1e4)   (never pay more than buffered up)
    #     SELL limit >= quote * (1 - bps/1e4)   (never sell below buffered down)
    # When `limit_buffer_bps == 0` the order is a MARKET order (legacy default)
    # and no `limit_offset_bps` is emitted — byte-identical to prior behaviour.
    # -----------------------------------------------------------------------
    use_limit = limit_buffer_bps > 0
    order_type = OrderType.LIMIT if use_limit else OrderType.MARKET

    client_order_id = make_client_order_id(
        strategy_id, symbol, side.value, gate_qty, timestamp=now,
    )
    intent = OrderIntent(
        strategy_id=strategy_id,
        symbol=symbol,
        side=side,
        qty=gate_qty,
        order_type=order_type,
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

    out: Dict[str, Any] = {
        "client_order_id": client_order_id,
        "symbol": symbol,
        "action": action,
        "side": side.value,
        "qty": qty,
        "target_notional": target_notional,
        "order_type": order_type.value,
        # Resolved DOWNSTREAM from a live MCP quote at review time (see the
        # LIMIT-PRICE CONTRACT block above); always null in the headless queue.
        "limit_price": None,
        "conviction": round(conviction, 4),
        "gate_allowed": gate_allowed,
        "gate_reasons": gate_reasons,
        "allow_place": allow_place,
        # The operator-facing "why" a reviewer reads before approving. This USED
        # to read `rec.strategy or rec.rationale` — but an advisory Recommendation
        # carries BOTH a `strategy` label AND a `rationale` paragraph, and the
        # truthy label short-circuited the `or`, so the real reasoning (the
        # engine's plain-English "why", also written to state_snapshot.json) was
        # silently discarded at the queue boundary and every reviewed order showed
        # a bare label. Now `rationale` carries the actual reasoning; the owner
        # label moves to its own `strategy` key so nothing is lost (`strategy_id`
        # is NOT in this dict — `rationale` was the label's only home). Fall back
        # to the label only when there's genuinely no rationale text, never empty.
        "rationale": _truncate_rationale(
            str(getattr(rec, "rationale", "") or getattr(rec, "strategy", ""))
        ),
        # The strategy/owner label (advisory: "advisory"; a follow: "Follow:<id>").
        # Additive key — readers that don't know it (gui _coerce_intent) ignore it.
        "strategy": str(getattr(rec, "strategy", "")),
    }
    if use_limit:
        # Only present on LIMIT intents — absent for MARKET so buffer==0 output
        # is byte-identical to the legacy MARKET-only queue.
        out["limit_offset_bps"] = limit_buffer_bps
    return out


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
        kill_switch_active = True
        logger.warning("queue_builder: kill-switch check failed (%s); assuming ACTIVE for safety", exc)

    max_notional = _max_notional()
    limit_buffer_bps = _limit_buffer_bps()
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
                limit_buffer_bps=limit_buffer_bps,
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
        "limit_buffer_bps": limit_buffer_bps,
        "n_intents": len(intents),
        "n_placeable": sum(1 for i in intents if i["allow_place"]),
        "intents": intents,
    }


_NOTIFIED_FILENAME = "execution_queue_notified.json"


def _intent_notify_key(intent: Dict[str, Any]) -> str:
    """Identity used to detect a genuinely NEW (or newly-placeable) intent.

    Deliberately excludes ``client_order_id``: that id is bucketed by a 60s
    timestamp (`execution.order_manager.make_client_order_id`), so it changes
    every cycle even for an unchanged recommendation — keying on it would push
    a duplicate notification on every `--interval` tick. Including
    ``allow_place`` means a recommendation that was blocked and later clears
    the gate (or the kill switch is lifted) is treated as notify-worthy again.
    """
    return f"{intent.get('symbol')}:{intent.get('side')}:{intent.get('allow_place')}"


_NOTIFIED_STATE_DEFAULTS: Dict[str, Any] = {
    "keys": [],
    "last_notified_at": None,
    "last_notified_title": None,
    "last_notified_count": None,
    "last_notified_priority": None,
}


def _load_notified_state(path: Path) -> Dict[str, Any]:
    """Read the notify-dedup sidecar; tolerant of missing/corrupt/legacy files.

    The GUI's `gui/robinhood_execution_panel.py` reads this same file
    (read-only) to render a "last notification sent" indicator, so its schema
    is a small public contract, not purely internal state.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):  # pre-GUI-indicator format: a bare key list
            return {**_NOTIFIED_STATE_DEFAULTS, "keys": raw}
        if isinstance(raw, dict):
            return {**_NOTIFIED_STATE_DEFAULTS, **raw}
    except Exception:
        pass
    return dict(_NOTIFIED_STATE_DEFAULTS)


def _save_notified_state(path: Path, state: Dict[str, Any]) -> None:
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        logger.debug("queue_builder: failed to persist notified-state sidecar (%s)", exc)


def _notify_new_intents(payload: Dict[str, Any], output_dir: Path) -> None:
    """Push an ntfy notification when the queue holds a genuinely new intent.

    Best-effort and silent: `alerting.notify` is itself a no-op when
    `NTFY_TOPIC` is unset, and any failure here is caught so a notification
    problem can never affect whether the queue file itself was written. When a
    push is attempted, records `last_notified_at`/`title`/`count`/`priority`
    in the sidecar (timestamped from `payload["generated_at"]`, this build's
    own clock) so the GUI can show "last notification sent" without needing
    its own timer.
    """
    intents = payload.get("intents") or []
    if not intents:
        return
    try:
        from alerting import notify
    except Exception as exc:
        logger.debug("queue_builder: alerting import failed (%s); skipping notify", exc)
        return

    sidecar = output_dir / _NOTIFIED_FILENAME
    state = _load_notified_state(sidecar)
    prior_keys = set(state.get("keys") or [])
    current_keys = {_intent_notify_key(i) for i in intents}
    new_intents = [i for i in intents if _intent_notify_key(i) not in prior_keys]

    if new_intents:
        placeable = [i for i in new_intents if i["allow_place"]]
        lines = [f"Mode: {payload['mode']}  |  {len(new_intents)} new of {len(intents)} queued"]
        for i in new_intents[:5]:
            notional = i["target_notional"] or 0.0
            flag = "  READY TO PLACE" if i["allow_place"] else ""
            lines.append(
                f"  {i['action']:<4} {i['symbol']:<6} ${notional:,.0f}  "
                f"conv={i['conviction']:.2f}{flag}"
            )
        if len(new_intents) > 5:
            lines.append(f"  … +{len(new_intents) - 5} more")
        title = "InvestYo — Trades Ready to Place" if placeable else "InvestYo — New Trade Proposals"
        priority = "high" if placeable else "default"
        try:
            notify(title, "\n".join(lines), priority=priority)
        except Exception as exc:
            logger.debug("queue_builder: notify() raised unexpectedly (%s)", exc)
        state["last_notified_at"] = payload.get("generated_at")
        state["last_notified_title"] = title
        state["last_notified_count"] = len(new_intents)
        state["last_notified_priority"] = priority

    state["keys"] = sorted(current_keys)
    _save_notified_state(sidecar, state)


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
    except Exception as exc:
        logger.warning("queue_builder: failed to emit execution queue (%s); skipping", exc)
        return None

    try:
        _notify_new_intents(payload, output_dir)
    except Exception as exc:
        logger.debug("queue_builder: notify step failed (%s); queue file is unaffected", exc)

    return path

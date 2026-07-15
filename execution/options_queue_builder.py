"""Tier 8 gated, dry-run OPTIONS execution-queue builder — the seam between the headless pipeline and the Robinhood Trading MCP for multi-leg option intents. Sources premium-selling directives, keeps only gate-passing ones, screens each through PreTradeRiskGate + GlobalKillSwitch in dry-run, and writes output/options_execution_queue.json. Never contacts a broker; allow_place is structurally False outside live mode."""

# =============================================================================
# MODULE: OPTIONS EXECUTION QUEUE BUILDER  (Tier 8 — Robinhood options bridge)
# File: execution/options_queue_builder.py
#
# Sibling of ``execution/queue_builder.py`` (READ THAT FIRST) — same gating
# philosophy and safety invariants, but for OPTIONS premium-selling directives
# instead of equity orders.  It lives INSIDE the sanctioned ``execution/``
# order-code zone and is the seam between the headless advisory pipeline and the
# Robinhood Trading MCP for MULTI-LEG option intents.  The pipeline cannot call
# MCP tools (those are LLM-agent tools), so this module emits a GATED, DRY-RUN
# list of proposed multi-leg option INTENTS to
# ``output/options_execution_queue.json``.  A separate Claude Code agent reads
# that file and is the only actor that ever calls the MCP option-placement
# tools.
#
# This module NEVER contacts a broker and NEVER places an order.  It only:
#   1. Sources a premium-selling directive per symbol from
#      ``technical_options_engine.build_premium_directive`` (which already
#      applies the VRP / regime gate internally).
#   2. Keeps ONLY directives that pass the codebase's premium-selling gate:
#         true_ivr (IVR proxy) > 50, VRP > 0.02 (when known), VIX < 30,
#         NOT a CREDIT EVENT / RECESSION regime,
#         AND ``validate_directive_integrity(...)["ok"] is True``.
#      Cash / Wait directives are dropped.
#   3. Runs each surviving directive through the existing ``PreTradeRiskGate`` +
#      ``GlobalKillSwitch`` (the same decision stack the equity path uses), in
#      dry-run, and writes the gated queue to disk — but ONLY when the execution
#      mode is ``review`` or ``live``.  In the default ``off`` mode nothing is
#      written.
#
# Safety invariant: ``allow_place`` is computed here as
#       mode == "live"  AND  gate passed  AND  kill switch clear
#       AND  a per-order notional cap is configured.
# It is therefore STRUCTURALLY False in every non-live posture.  The downstream
# agent treats ``allow_place=false`` as "preview only".
#
# Function names here deliberately avoid the order-submission tokens the
# repo-wide AST guard (``tests/test_pipeline_smoke.py::TestNoOrderFunctions``)
# forbids — there is no ``place_*`` / ``submit_order`` / ``*_order`` definition.
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
    # Premium-selling gate — mirrors the codebase-wide VRP regime rules
    # (see CLAUDE.md "Options premium selling ... gated by VRP regime rules").
    "min_ivr": 50.0,          # true_ivr / IVR proxy must EXCEED this
    "min_vrp": 0.02,          # VRP must EXCEED this (checked only when VRP known)
    "max_vix": 30.0,          # VIX must be strictly BELOW this
    # Regimes that unconditionally veto premium selling.
    "blocked_regimes": ("CREDIT EVENT", "RECESSION"),
    # Directive DTE stamped on every leg (Robinhood expiration is resolved by
    # the downstream agent from a LIVE option chain at review time).
    "target_dte": 30,
    # strategy_id stamped on every intent (drives the deterministic order id).
    "strategy_id": "options_advisory",
}

_QUEUE_FILENAME = "options_execution_queue.json"


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
    own documented behaviour) — this is a PRE-SCREEN.  Robinhood's own option
    order review plus per-trade human confirmation are the authoritative checks
    downstream.  ``is_premium_sell_strategy=True`` so the gate's
    minimum-validation / stress-aware checks treat these as short-vol intents.
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
            logger.debug("options_queue_builder: skipping position in risk context (%s)", exc)

    account = BrokerAccountSnapshot(equity=equity, cash=buying_power, buying_power=buying_power)
    return RiskContext(
        macro=None,
        open_positions=positions,
        account=account,
        returns_df=None,
        start_of_day_equity=equity or None,
        is_premium_sell_strategy=True,
        current_prices=current_prices,
        timestamp=now,
    )


def passes_premium_gate(
    directive: Dict[str, Any],
    *,
    macro_dto: Optional[Any],
    vrp: Optional[float],
    config: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    """Apply the codebase-wide premium-selling gate to one hydrated directive.

    Returns ``(passed, reasons)`` — ``reasons`` lists the failing conditions when
    blocked, empty when it passes.  Conditions (all must hold):

      * The directive is actionable (not Cash / Wait) — the engine already
        returns Cash/Wait when its OWN internal VRP / regime gate fired, so a
        non-Cash directive means the engine-level gate already passed.
      * IVR proxy ``> min_ivr`` (defaults to 50).
      * VRP ``> min_vrp`` **only when VRP is known**; ``vrp=None`` (no option
        chain) is treated as "not disqualifying" here because true VRP requires
        an options chain the headless pipeline does not have — the engine's own
        gate plus the IVR / VIX / regime checks carry the load (CONSTRAINT #4:
        no fabricated VRP value).
      * VIX ``< max_vix`` (defaults to 30) when a macro DTO is supplied.
      * Regime not in ``blocked_regimes`` (CREDIT EVENT / RECESSION).
      * ``Integrity_OK is True`` (strike grid + delta-target tolerance).

    Fails CLOSED on any exception.
    """
    reasons: List[str] = []
    try:
        strategy = str(directive.get("Strategy", "Cash"))
        action = str(directive.get("Action", "Wait"))
        legs = directive.get("Legs", []) or []

        # Cash / Wait or empty-leg directives are dropped outright.
        if strategy.strip().lower().startswith("cash") or action.strip().lower() == "wait" or not legs:
            reasons.append("cash_or_wait_directive")
            return False, reasons

        ivr = _f(directive.get("IVR_Proxy"), default=float("nan"))
        if not math.isfinite(ivr) or ivr <= float(config["min_ivr"]):
            reasons.append(f"ivr_not_above_{config['min_ivr']:g}")

        if vrp is not None:
            vrp_val = _f(vrp, default=float("nan"))
            if not math.isfinite(vrp_val) or vrp_val <= float(config["min_vrp"]):
                reasons.append(f"vrp_not_above_{config['min_vrp']:g}")

        if macro_dto is not None:
            vix = _f(getattr(macro_dto, "vix", None), default=float("nan"))
            if math.isfinite(vix) and vix >= float(config["max_vix"]):
                reasons.append(f"vix_at_or_above_{config['max_vix']:g}")
            regime = str(getattr(macro_dto, "market_regime", "") or "").upper()
            if regime in {r.upper() for r in config["blocked_regimes"]}:
                reasons.append(f"blocked_regime:{regime}")

        if not bool(directive.get("Integrity_OK", False)):
            reasons.append("integrity_failed")

        return (not reasons), reasons
    except Exception as exc:  # noqa: BLE001 — fail closed
        logger.warning("options_queue_builder: premium gate raised (%s); failing closed", exc)
        return False, [f"gate_error: {exc}"]


def gate_intent(
    intent: OrderIntent,
    context: RiskContext,
    gate: Optional[PreTradeRiskGate] = None,
) -> Tuple[bool, List[str]]:
    """Run the pre-trade risk gate for one (proxy) option intent.

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
        logger.warning("options_queue_builder: risk gate raised for %s (%s); failing closed",
                       getattr(intent, "symbol", "?"), exc)
        return False, [f"gate_error: {exc}"]


def _leg_dicts(directive: Dict[str, Any], target_dte: int) -> List[Dict[str, Any]]:
    """Normalise the engine's ``Legs`` payload into queue-intent leg dicts.

    Each leg: ``side`` (buy/sell — Robinhood option order convention), ``strike``,
    ``delta``, ``option_type`` (call/put), ``dte``, ``expiration`` (None — the
    downstream agent resolves the exact expiry from a live chain), ``price``
    (the engine's theoretical BS price, informational only).
    """
    out: List[Dict[str, Any]] = []
    for leg in directive.get("Legs", []) or []:
        engine_side = str(leg.get("Side", "")).strip().lower()  # "short" / "long"
        # Short leg = sell to open; long leg = buy to open.
        order_side = "sell" if engine_side == "short" else "buy"
        opt_type = str(leg.get("Type", "")).strip().lower()  # "put" / "call"
        strike = _f(leg.get("Strike"), default=float("nan"))
        delta = leg.get("Delta", None)
        out.append({
            "side": order_side,
            "position_effect": "open",
            "option_type": opt_type,
            "strike": round(strike, 2) if math.isfinite(strike) else None,
            "delta": round(_f(delta), 4) if delta is not None else None,
            "dte": int(target_dte),
            "expiration": None,  # resolved by the downstream agent from a live chain
            "price": _f(leg.get("Price"), default=float("nan")) if leg.get("Price") is not None else None,
        })
    return out


def _intent_dict(
    symbol: str,
    directive: Dict[str, Any],
    snapshot: Any,
    *,
    mode: str,
    kill_switch_active: bool,
    max_notional: float,
    gate: PreTradeRiskGate,
    context: RiskContext,
    now: datetime,
    macro_dto: Optional[Any],
    vrp: Optional[float],
    config: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Translate one hydrated premium directive into a gated multi-leg intent.

    Returns ``None`` when the directive fails the premium-selling gate (dropped).
    Otherwise returns the full intent dict with ``allow_place`` structurally
    False unless mode==live AND the risk gate passed AND the kill switch is clear
    AND a per-order notional cap is configured.
    """
    symbol = str(symbol or "").upper()
    if not symbol:
        return None

    passed, gate_reasons_premium = passes_premium_gate(
        directive, macro_dto=macro_dto, vrp=vrp, config=config,
    )
    if not passed:
        logger.debug("options_queue_builder: %s dropped (%s)", symbol, gate_reasons_premium)
        return None

    strategy = str(directive.get("Strategy", "Cash"))
    action = str(directive.get("Action", "Wait"))
    net_premium = _f(directive.get("Net_Premium"), default=float("nan"))
    legs = _leg_dicts(directive, int(config["target_dte"]))
    if not legs:
        return None

    # Multi-leg option intents sell ONE spread contract (1 unit) by default; the
    # downstream agent scales the quantity against the notional cap at review
    # time using a LIVE chain.  ``target_notional`` caps the max risk we expose.
    target_notional: Optional[float] = None
    if max_notional > 0:
        target_notional = round(max_notional, 2)

    # Proxy equity intent purely for the PreTradeRiskGate pre-screen — the gate
    # operates on OrderIntent (equity-shaped).  This proxy NEVER leaves this
    # module; only its allow/deny verdict is used.  qty=1 keeps it minimal.
    strategy_id = str(config["strategy_id"])
    client_order_id = make_client_order_id(
        strategy_id, symbol, OrderSide.SELL.value, 1.0, timestamp=now,
    )
    proxy_intent = OrderIntent(
        strategy_id=strategy_id,
        symbol=symbol,
        side=OrderSide.SELL,
        qty=1.0,
        order_type=OrderType.MARKET,
        client_order_id=client_order_id,
        dry_run=True,
    )
    gate_allowed, gate_reasons = gate_intent(proxy_intent, context, gate)

    notional_cap_ok = max_notional > 0  # live REQUIRES a configured cap
    if mode == "live" and not notional_cap_ok and "notional_cap_unset" not in gate_reasons:
        gate_reasons = gate_reasons + ["notional_cap_unset"]

    allow_place = bool(
        mode == "live"
        and gate_allowed
        and not kill_switch_active
        and notional_cap_ok
    )

    return {
        "client_order_id": client_order_id,
        "symbol": symbol,
        "strategy": strategy,
        "action": action,
        "legs": legs,
        "net_premium": round(net_premium, 4) if math.isfinite(net_premium) else None,
        "target_notional": target_notional,
        "order_type": "net_credit" if _f(net_premium) > 0 else "net_debit",
        "conviction": round(_f(directive.get("IVR_Proxy")) / 100.0, 4),
        "gate_allowed": gate_allowed,
        "gate_reasons": gate_reasons,
        "allow_place": allow_place,
        "integrity_ok": bool(directive.get("Integrity_OK", False)),
        "rationale": (
            f"{action} {strategy} | IVR≈{_f(directive.get('IVR_Proxy')):.0f} "
            f"σ_GARCH={_f(directive.get('Sigma_GARCH')):.2f} "
            f"bias={directive.get('Trend_Bias', 'Neutral')}"
        )[:240],
    }


def _resolve_symbols(run_result: Any) -> List[str]:
    """Union of held-position symbols and recommendation symbols (sorted, deduped)."""
    syms: set[str] = set()
    snapshot = getattr(run_result, "snapshot", None)
    raw_positions = getattr(snapshot, "positions", {}) or {}
    if isinstance(raw_positions, dict):
        for k in raw_positions.keys():
            if k:
                syms.add(str(k).upper())
    for rec in getattr(run_result, "recommendations", []) or []:
        s = str(getattr(rec, "symbol", "") or "").upper()
        if s:
            syms.add(s)
    return sorted(syms)


def _directive_for_symbol(
    symbol: str,
    *,
    market: Any,
    macro_dto: Optional[Any],
    vrp: Optional[float],
    target_dte: int,
) -> Optional[Dict[str, Any]]:
    """Fetch bars + quote and compute a premium directive for one symbol.

    Returns ``None`` on any per-symbol failure (dead-letter resilient — one bad
    symbol never aborts the queue build).
    """
    if market is None:
        return None
    from technical_options_engine import build_premium_directive  # lazy import
    try:
        quote = market.get_latest_quote(symbol)
        spot = _f(getattr(quote, "price", None), default=float("nan"))
        is_stale = bool(getattr(quote, "is_stale", True))
    except Exception as exc:  # noqa: BLE001
        logger.debug("options_queue_builder: quote fetch failed for %s (%s)", symbol, exc)
        return None
    if not math.isfinite(spot) or spot <= 0:
        return None
    try:
        bars = market.get_intraday_bars(symbol, lookback_days=450)
    except Exception as exc:  # noqa: BLE001
        logger.debug("options_queue_builder: bar fetch failed for %s (%s)", symbol, exc)
        return None
    try:
        return build_premium_directive(
            symbol,
            bars,
            spot_price=spot,
            is_stale=is_stale,
            target_dte=int(target_dte),
            macro_dto=macro_dto,
            vrp=vrp,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("options_queue_builder: directive build failed for %s (%s)", symbol, exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_options_execution_queue(
    run_result: Any,
    *,
    mode: Optional[str] = None,
    market: Any = None,
    macro_dto: Optional[Any] = None,
    directives: Optional[Dict[str, Dict[str, Any]]] = None,
    vrp: Optional[float] = None,
    config: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build the gated OPTIONS execution-queue payload from a `RunResult`.

    Runs the premium-selling gate + risk gate + kill-switch check but NEVER
    contacts a broker or the MCP.  Per-symbol failures are logged and skipped
    (dead-letter resilient).

    Parameters
    ----------
    run_result :
        Advisory `RunResult` (source of the symbol universe and account snapshot).
    mode :
        Execution mode override (off / review / live); falls back to
        ``settings.ROBINHOOD_EXECUTION_MODE``.
    market :
        A `MarketDataProvider` used to fetch quotes + bars per symbol.  When
        ``None`` and no ``directives`` are supplied, the module-level singleton
        ``data.market_data.get_provider()`` is used.  Injectable for offline tests.
    macro_dto :
        Optional macro DTO forwarded to `build_premium_directive` AND consulted
        for the VIX / regime gate.  ``None`` leaves those two checks to pass.
    directives :
        Optional pre-computed ``{symbol: directive_dict}`` mapping.  When
        supplied, the module skips market fetches entirely and gates these
        directly (the primary offline-test seam).
    vrp :
        Optional Volatility Risk Premium; when known, ``vrp > 0.02`` is enforced.
    config, now :
        Threshold overrides and a frozen clock for determinism.
    """
    cfg = {**CONFIG, **(config or {})}
    resolved_mode = _resolve_mode(mode)
    now = now or datetime.now(timezone.utc)
    snapshot = getattr(run_result, "snapshot", None)

    kill_switch_active = False
    try:
        kill_switch_active = GlobalKillSwitch().is_active()
    except Exception as exc:
        kill_switch_active = True
        logger.warning("options_queue_builder: kill-switch check failed (%s); assuming ACTIVE for safety", exc)

    max_notional = _max_notional()
    gate = PreTradeRiskGate()
    context = _build_risk_context(snapshot, now)

    # Resolve the directive source: explicit map (tests) or live per-symbol fetch.
    resolved_directives: Dict[str, Dict[str, Any]] = {}
    if directives is not None:
        resolved_directives = {str(k).upper(): v for k, v in directives.items()}
    else:
        active_market = market
        if active_market is None:
            try:
                from data.market_data import get_provider
                active_market = get_provider()
            except Exception as exc:
                logger.warning("options_queue_builder: no market provider (%s); empty queue", exc)
                active_market = None
        for sym in _resolve_symbols(run_result):
            d = _directive_for_symbol(
                sym, market=active_market, macro_dto=macro_dto,
                vrp=vrp, target_dte=int(cfg["target_dte"]),
            )
            if d is not None:
                resolved_directives[sym] = d

    intents: List[Dict[str, Any]] = []
    for sym, directive in resolved_directives.items():
        try:
            d = _intent_dict(
                sym, directive, snapshot,
                mode=resolved_mode,
                kill_switch_active=kill_switch_active,
                max_notional=max_notional,
                gate=gate,
                context=context,
                now=now,
                macro_dto=macro_dto,
                vrp=vrp,
                config=cfg,
            )
            if d is not None:
                intents.append(d)
        except Exception as exc:
            logger.warning("options_queue_builder: failed to build intent for %s (%s)", sym, exc)

    return {
        "generated_at": now.isoformat(),
        "queue_type": "options",
        "mode": resolved_mode,
        "kill_switch_active": kill_switch_active,
        "max_notional_per_order": max_notional,
        "n_intents": len(intents),
        "n_placeable": sum(1 for i in intents if i["allow_place"]),
        "intents": intents,
    }


def emit_options_execution_queue(
    run_result: Any,
    *,
    mode: Optional[str] = None,
    output_dir: Optional[Path] = None,
    market: Any = None,
    macro_dto: Optional[Any] = None,
    directives: Optional[Dict[str, Dict[str, Any]]] = None,
    vrp: Optional[float] = None,
    config: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """Build and atomically write ``output/options_execution_queue.json``.

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
        payload = build_options_execution_queue(
            run_result,
            mode=resolved_mode,
            market=market,
            macro_dto=macro_dto,
            directives=directives,
            vrp=vrp,
            config=config,
            now=now,
        )
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
            "Options execution queue written (mode=%s, intents=%d, placeable=%d) → %s",
            resolved_mode, payload["n_intents"], payload["n_placeable"], path,
        )
        return path
    except Exception as exc:
        logger.warning("options_queue_builder: failed to emit options queue (%s); skipping", exc)
        return None

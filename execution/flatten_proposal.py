"""Gated, dry-run flatten-on-kill proposal builder inside the sanctioned execution/ zone. When the kill switch activates with FLATTEN_ON_KILL=True it writes a human-reviewable JSON of position-closing intents (each dry_run=True, pre-screened through PreTradeRiskGate for annotation only). It never contacts a broker; allow_place is structurally False on every intent."""

# =============================================================================
# MODULE: FLATTEN-ON-KILL PROPOSAL  (gated, dry-run — placement-INCAPABLE)
# File: execution/flatten_proposal.py
#
# This module lives INSIDE the sanctioned `execution/` order-code zone.  When
# the global kill switch activates with ``settings.FLATTEN_ON_KILL=True`` it
# builds a *human-reviewable proposal* of position-closing intents and writes it
# as a GATED, DRY-RUN JSON to ``output/flatten_proposal.json``.  It exists to
# replace the previous log-only "manually close all positions" reminder with a
# concrete, reviewable artifact.
#
# It mirrors `execution/queue_builder.py`'s posture EXACTLY:
#   * It NEVER contacts a broker and NEVER places an order.
#   * Every closing intent is constructed with ``dry_run=True``.
#   * Each intent is pre-screened through the SAME `PreTradeRiskGate` in dry-run
#     purely to annotate ``gate_allowed`` / ``gate_reasons`` for the reviewer.
#   * ``allow_place`` is STRUCTURALLY False on every flatten intent: this
#     proposal is only ever emitted BECAUSE the kill switch is active, and an
#     active kill switch forbids all placement by definition.  There is no
#     "live" auto-flatten path anywhere in this module.
#
# Function names here deliberately avoid the order-submission tokens the
# repo-wide AST guard (`tests/test_pipeline_smoke.py::TestNoOrderFunctions`)
# forbids — there is no `place_*` / `submit_order` / `*_order` definition.  The
# guard EXCLUDES `execution/`, but this module stays clean of those tokens
# anyway, matching `queue_builder`'s convention.
#
# The final actor that ever *acts* on this proposal is a human operator (or the
# `robinhood-execution` skill with per-trade confirmation), never this module.
# =============================================================================

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from execution.broker_base import (
    AccountSnapshot as BrokerAccountSnapshot,
    OrderIntent,
    OrderSide,
    OrderType,
    PositionSnapshot,
)
from execution.order_manager import make_client_order_id
from execution.risk_gate import PreTradeRiskGate, RiskContext

# Reuse queue_builder's gating helper verbatim so both bridges pre-screen intents
# through an identical code path (fails CLOSED on any gate exception).
from execution.queue_builder import gate_intent

logger = logging.getLogger(__name__)

_PROPOSAL_FILENAME = "flatten_proposal.json"

# Single source of truth for this bridge's metadata (no magic strings below).
CONFIG: Dict[str, Any] = {
    # strategy_id stamped on every closing intent (drives the deterministic id).
    "strategy_id": "flatten_on_kill",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _f(x: Any, default: float = 0.0) -> float:
    """Coerce to a finite float; return ``default`` on failure / NaN / inf."""
    import math
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _flatten_enabled(explicit: Optional[bool]) -> bool:
    """Resolve the FLATTEN_ON_KILL gate — explicit arg wins, else settings."""
    if explicit is not None:
        return bool(explicit)
    try:
        from settings import settings  # local import — avoid import cycle
        return bool(getattr(settings, "FLATTEN_ON_KILL", False))
    except Exception:
        return False


def _position_view(pos: Any) -> Optional[Dict[str, float]]:
    """Duck-type a position object into a uniform view, or ``None`` to skip.

    Accepts both ``PositionSnapshot`` (broker_base: ``qty`` / ``avg_entry_price``)
    and ``PortfolioPosition`` (robinhood_portfolio: ``quantity`` /
    ``average_cost`` / ``current_price``).  Zero-quantity holdings are skipped
    (nothing to flatten — no fabricated position).
    """
    try:
        symbol = str(getattr(pos, "symbol", "")).upper()
        if not symbol:
            return None
        qty = _f(getattr(pos, "quantity", None) if hasattr(pos, "quantity")
                 else getattr(pos, "qty", 0.0))
        if qty == 0.0:
            return None
        price = _f(getattr(pos, "current_price", 0.0))
        avg = _f(getattr(pos, "avg_entry_price", None) if hasattr(pos, "avg_entry_price")
                 else getattr(pos, "average_cost", 0.0))
        market_value = _f(getattr(pos, "market_value", 0.0))
        unrealized_pl = _f(getattr(pos, "unrealized_pl", 0.0))
        return {
            "symbol": symbol,
            "qty": qty,
            "price": price,
            "avg": avg,
            "market_value": market_value,
            "unrealized_pl": unrealized_pl,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("flatten_proposal: skipping unparseable position (%s)", exc)
        return None


def _build_risk_context(views: List[Dict[str, float]], now: datetime) -> RiskContext:
    """Best-effort `RiskContext` from the current position views.

    Missing data leaves each check to conservative-pass (the gate's documented
    behaviour).  This is a PRE-SCREEN only — the closing intents can never be
    placed by this module regardless of the gate outcome.
    """
    positions: List[PositionSnapshot] = []
    current_prices: Dict[str, float] = {}
    equity = 0.0
    for v in views:
        positions.append(PositionSnapshot(
            symbol=v["symbol"],
            qty=v["qty"],
            avg_entry_price=v["avg"],
            market_value=v["market_value"],
            unrealized_pl=v["unrealized_pl"],
        ))
        equity += v["market_value"]
        if v["price"] > 0:
            current_prices[v["symbol"]] = v["price"]

    account = BrokerAccountSnapshot(equity=equity, cash=0.0, buying_power=0.0)
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


def _closing_intent_dict(
    view: Dict[str, float],
    *,
    gate: PreTradeRiskGate,
    context: RiskContext,
    now: datetime,
    strategy_id: str,
) -> Optional[Dict[str, Any]]:
    """Translate one position view into a gated, dry-run closing-intent dict.

    A LONG position (qty > 0) closes with a SELL of the full quantity; a SHORT
    position (qty < 0) closes with a BUY of the absolute quantity.  The intent
    is always ``dry_run=True`` and ``allow_place`` is always False (see module
    header — an active kill switch forbids placement by definition).
    """
    qty = view["qty"]
    if qty == 0.0:
        return None

    # Opposite side to flatten the exposure.
    side = OrderSide.SELL if qty > 0 else OrderSide.BUY
    close_qty = abs(qty)
    symbol = view["symbol"]

    client_order_id = make_client_order_id(
        strategy_id, symbol, side.value, close_qty, timestamp=now,
    )
    intent = OrderIntent(
        strategy_id=strategy_id,
        symbol=symbol,
        side=side,
        qty=close_qty,
        order_type=OrderType.MARKET,
        client_order_id=client_order_id,
        dry_run=True,
    )

    gate_allowed, gate_reasons = gate_intent(intent, context, gate)

    return {
        "client_order_id": client_order_id,
        "symbol": symbol,
        "action": side.value.upper(),   # SELL to close long, BUY to close short
        "side": side.value,
        "qty": close_qty,
        "target_notional": round(abs(view["market_value"]), 2),
        "order_type": OrderType.MARKET.value,
        "limit_price": None,
        "current_qty": qty,             # signed held quantity being closed
        "gate_allowed": gate_allowed,
        "gate_reasons": gate_reasons,
        # STRUCTURALLY False: this proposal only exists because the kill switch
        # is active, which forbids all placement.  There is no live path here.
        "allow_place": False,
        "rationale": "flatten-on-kill: close open exposure for human review",
    }


def _load_current_positions() -> List[Any]:
    """Load the most-recent held positions WITHOUT any network call.

    Reads the DB-persisted `AccountSnapshot` via `HistoricalStore`
    (`latest_account_snapshot()` — DB-first, no Robinhood login).  Fully
    dead-letter safe: returns ``[]`` on any failure so a flatten proposal is
    still emitted (with zero intents + an explanatory note) rather than raising
    inside the kill-switch activation path.
    """
    try:
        from data.historical_store import HistoricalStore  # lazy — avoid cycles
        snapshot = HistoricalStore().latest_account_snapshot()
        if snapshot is None:
            return []
        positions = getattr(snapshot, "positions", {}) or {}
        if isinstance(positions, dict):
            return list(positions.values())
        return list(positions)
    except Exception as exc:
        logger.debug("flatten_proposal: could not load current positions (%s)", exc)
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_flatten_proposal(
    positions: Any,
    *,
    reason: str = "",
    now: Optional[datetime] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the gated, dry-run flatten-proposal payload from held positions.

    Pure with respect to the broker — runs the risk gate in dry-run to annotate
    each intent but NEVER contacts a broker or the MCP.  ``positions`` is any
    iterable of position-like objects (``PositionSnapshot`` or
    ``PortfolioPosition``); per-position failures are logged and skipped
    (dead-letter resilient).
    """
    cfg = {**CONFIG, **(config or {})}
    now = now or datetime.now(timezone.utc)

    views: List[Dict[str, float]] = []
    for pos in (positions or []):
        v = _position_view(pos)
        if v is not None:
            views.append(v)

    gate = PreTradeRiskGate()
    context = _build_risk_context(views, now)

    intents: List[Dict[str, Any]] = []
    for v in views:
        try:
            d = _closing_intent_dict(
                v, gate=gate, context=context, now=now,
                strategy_id=str(cfg["strategy_id"]),
            )
            if d is not None:
                intents.append(d)
        except Exception as exc:
            logger.warning("flatten_proposal: failed to build intent for %s (%s)",
                           v.get("symbol", "?"), exc)

    return {
        "generated_at": now.isoformat(),
        "kind": "flatten_on_kill",
        "reason": reason,
        # Emitted only when the kill switch is active; recorded for the reviewer.
        "kill_switch_active": True,
        "dry_run": True,
        "n_intents": len(intents),
        # Always 0 — every flatten intent is structurally preview-only.
        "n_placeable": sum(1 for i in intents if i["allow_place"]),
        "intents": intents,
        "note": (
            "PROPOSAL ONLY — no order was placed. Review and close positions "
            "manually (or via the robinhood-execution skill with per-trade "
            "confirmation). This platform is advisory-only."
        ),
    }


def emit_flatten_proposal(
    positions: Any = None,
    *,
    reason: str = "",
    output_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
    flatten_enabled: Optional[bool] = None,
) -> Optional[Path]:
    """Build and atomically write ``output/flatten_proposal.json``.

    Gated behind ``settings.FLATTEN_ON_KILL`` (override via ``flatten_enabled``):
    returns ``None`` and writes NOTHING when the flag is off (the default) — so
    there is zero behavioural change for the common case.  When ``positions`` is
    ``None`` the current held positions are loaded DB-first (no network) via
    ``_load_current_positions()``.

    Never raises (CONSTRAINT #6): a write failure is logged and swallowed so the
    kill-switch activation path is never destabilised by this bridge.
    """
    if not _flatten_enabled(flatten_enabled):
        return None

    try:
        if positions is None:
            positions = _load_current_positions()
        payload = build_flatten_proposal(positions, reason=reason, now=now)
        if output_dir is None:
            from settings import settings
            output_dir = Path(settings.OUTPUT_DIR)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / _PROPOSAL_FILENAME
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
        logger.critical(
            "FLATTEN-ON-KILL proposal written (dry-run, %d intents, 0 placeable) "
            "→ %s. NO order was placed — review manually.",
            payload["n_intents"], path,
        )
        return path
    except Exception as exc:
        logger.warning("flatten_proposal: failed to emit proposal (%s); skipping", exc)
        return None

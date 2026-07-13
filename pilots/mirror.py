"""Gated auto-mirror for Stockpy "Pilots" — turn *Follow Pilot P with $A* into a
proportional, target-notional **rebalance** queue (BUY to add, SELL to trim) that
flows through the EXISTING gated, dry-run execution bridge
(``execution/queue_builder.py``).

This module is the *write* side of the Pilot layer (Phase 3 of the Autopilot
remodel). It NEVER contacts a broker and defines NO order-submission function
(the repo-wide AST guard ``tests/test_pipeline_smoke.py::TestNoOrderFunctions``
forbids ``place_*`` / ``submit_order`` / ``*_order`` symbol names). All actual
placement remains the sole job of the downstream ``robinhood-execution`` skill,
which previews via ``review_equity_order`` and only ever places in ``live`` mode
with explicit per-trade human confirmation.

Flow
----
``build_follow_intents(pilot, amount, account_snapshot, snapshot=None)``
    Compute the Pilot's live holdings (:func:`pilots.scoring.pilot_holdings`),
    then for each holding a **target notional** ``target_i = amount * weight_i``
    (clamped by ``settings.ROBINHOOD_MAX_NOTIONAL_PER_ORDER`` when set). This is a
    *rebalance to target*, not a blind buy: the follower's CURRENT market value in
    that name (from ``account_snapshot.positions``) is netted off, so the order is
    sized to the delta ``target_i - current_i`` — a **BUY** when underweight, a
    **SELL** (partial trim) when overweight, and nothing when already within a
    small no-trade band. Each intent is a lightweight :class:`FollowIntent`
    carrying exactly the attributes ``execution.queue_builder._intent_dict`` reads
    off a ``Recommendation``: ``action`` (``"BUY"``/``"SELL"``), ``symbol``,
    ``strategy=f"Follow:{pilot.id}"``, ``conviction``, and — crucially —
    ``suggested_position_pct = |delta_i| / total_equity`` so the builder's own
    ``notional = equity * pct`` math reproduces the order notional verbatim (for
    BOTH sides: a SELL carrying a positive ``suggested_position_pct`` is the
    builder's partial-trim signal, resolved to a share count downstream and capped
    at the held quantity — see ``execution/queue_builder.py::_intent_dict``).

    **Rebalance scope (honest limitation):** rebalancing is confined to the
    Pilot's CURRENT holding set. A name the follower holds that is NOT in the
    Pilot's holdings is left untouched — fully exiting it would require per-follow
    position attribution (knowing which shares came from this follow vs. bought
    for other reasons), which is not modelled. So a Pilot that fully drops a name
    stops topping it up; it does not force-sell the follower's existing position.

``plan_follow(pilot, amount, account_snapshot, snapshot=None)``
    Wrap the intents in a ``RunResult``-shaped object (``.recommendations`` +
    ``.snapshot``) and hand it to ``execution.queue_builder.emit_execution_queue``,
    returning a small serialisable summary dict. Idempotent, gated, paper-first:
    when ``ROBINHOOD_EXECUTION_MODE`` is ``off`` (the default) NOTHING is written
    but the ``planned_intents`` preview is still returned.

Decision D3 — conviction / ``min_conviction``
---------------------------------------------
``execution.queue_builder`` drops any recommendation whose ``conviction`` is
below its ``min_conviction`` gate (default ``0.85``). A "follow" mirrors the
Pilot's *whole* holdings list proportionally — those holdings were already
filtered to positive-blend top-N inside ``pilot_holdings`` — so filtering again
by conviction would be wrong, and inflating every intent's conviction to clear
the 0.85 gate would be dishonest (CONSTRAINT #4). Per Decision D3 we therefore
set each intent's ``conviction`` to the Pilot's own **normalized target weight**
for that name (an honest proxy: the bigger the allocation, the higher the
conviction) and pass a low ``config["min_conviction"] = FOLLOW_MIN_CONVICTION``
(``0.0``) so the gate keeps every proportional holding rather than truncating the
tail. The pre-trade *risk* gate, kill switch, ``mode != "live"`` and the notional
cap all still apply downstream unchanged — ``min_conviction`` is not a safety
control, it is a relevance filter for the autonomous advisory backlog, and a
deliberate Follow has already chosen every name.

Dead-letter resilience (CONSTRAINT #6): every public function degrades to an
empty / preview-only result and never raises.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pilots.catalog import Pilot
from pilots.scoring import pilot_holdings

logger = logging.getLogger(__name__)

__all__ = [
    "FollowIntent",
    "FollowRunResult",
    "build_follow_intents",
    "plan_follow",
    "FOLLOW_MIN_CONVICTION",
]

# Decision D3 — pass this low floor as config["min_conviction"] so the queue
# keeps every proportional holding instead of dropping low-weight tail names.
# See the module docstring for the full rationale.
FOLLOW_MIN_CONVICTION: float = 0.0

# Rebalance no-trade band: skip an order whose delta (target - current) is within
# max(_REBALANCE_MIN_DELTA_USD, _REBALANCE_BAND_FRACTION * target) of zero, so a
# follow doesn't churn tiny corrections. Fraction-of-target keeps the band
# proportional to the position; the absolute floor avoids sub-dollar orders.
_REBALANCE_BAND_FRACTION: float = 0.05
_REBALANCE_MIN_DELTA_USD: float = 1.0

# Fallback per-order notional ceiling (USD) used ONLY if
# ``settings.ROBINHOOD_MAX_NOTIONAL_PER_ORDER`` cannot be read at all. The real
# setting exists (default ``0.0`` == "unset / no cap"), so this constant is a
# defensive last resort, not the normal path. ``None`` == no clamp.
_FALLBACK_MAX_NOTIONAL: Optional[float] = None


# ---------------------------------------------------------------------------
# Lightweight, broker-free intent object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FollowIntent:
    """A single proportional rebalance intent produced by a Pilot follow.

    ``action`` is ``"BUY"`` (underweight → add) or ``"SELL"`` (overweight → partial
    trim). Carries exactly the attributes ``execution.queue_builder._intent_dict``
    reads off a ``Recommendation`` (``action`` / ``symbol`` / ``conviction`` /
    ``suggested_position_pct`` / ``strategy``), plus a few preview-only fields
    (``target_notional`` / ``weight`` / ``price`` / ``score``) that the queue
    builder ignores but the API/UI surfaces. ``target_notional`` here is the
    notional of THIS order (the |delta|), i.e. how much to add or trim — not the
    absolute target position size. Deliberately NOT the heavy
    ``engine.advisory.Recommendation`` dataclass: a plain object keeps this
    write-path module free of any heavy-engine import.
    """

    symbol: str
    action: str  # "BUY" (add) or "SELL" (partial trim) for a follow-mirror
    strategy: str
    conviction: float
    suggested_position_pct: float
    target_notional: float
    weight: float
    price: Optional[float]
    score: Optional[float]
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serialisable preview dict (no broker fields)."""
        return {
            "symbol": self.symbol,
            "action": self.action,
            "strategy": self.strategy,
            "conviction": round(float(self.conviction), 6),
            "suggested_position_pct": round(float(self.suggested_position_pct), 8),
            "target_notional": round(float(self.target_notional), 2),
            "weight": round(float(self.weight), 6),
            "price": self.price,
            "score": self.score,
            "rationale": self.rationale,
        }


@dataclass
class FollowRunResult:
    """Minimal ``RunResult``-shaped carrier for ``emit_execution_queue``.

    ``execution.queue_builder`` reads only ``.recommendations`` (an iterable of
    recommendation-like objects) and ``.snapshot`` (the account snapshot, for the
    risk context + BUY notional math), both via ``getattr`` — so this tiny shim is
    all the builder needs.
    """

    recommendations: List[FollowIntent] = field(default_factory=list)
    snapshot: Any = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_float(value: Any) -> Optional[float]:
    """Coerce to a finite float, or ``None`` when not possible."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
        return None
    return f


def _max_notional_cap() -> Optional[float]:
    """Return the per-order notional cap in USD, or ``None`` when unset.

    Reads ``settings.ROBINHOOD_MAX_NOTIONAL_PER_ORDER`` LIVE at call time (so an
    operator change takes effect without a reimport). The setting's ``0.0``
    default means "unset / no cap" (the same convention ``queue_builder`` uses),
    which maps to ``None`` here.
    """
    try:
        from settings import settings
        cap = _coerce_float(getattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 0.0))
        if cap is not None and cap > 0:
            return cap
        return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("mirror: could not read ROBINHOOD_MAX_NOTIONAL_PER_ORDER (%s)", exc)
        return _FALLBACK_MAX_NOTIONAL


def _resolve_mode() -> str:
    """Return the resolved execution mode ("off" | "review" | "live").

    Reuses ``queue_builder._resolve_mode`` so this module stays in lockstep with
    the builder's own validation/fallback rules; degrades to ``"off"`` if that
    import fails.
    """
    try:
        from execution.queue_builder import _resolve_mode as _rm
        return _rm(None)
    except Exception:  # pragma: no cover - defensive
        try:
            from settings import settings
            m = str(getattr(settings, "ROBINHOOD_EXECUTION_MODE", "off") or "").strip().lower()
            return m if m in ("off", "review", "live") else "off"
        except Exception:
            return "off"


def _current_market_value(account_snapshot: Any, symbol: str) -> float:
    """Follower's CURRENT market value (USD) in ``symbol``, or ``0.0``.

    Reads ``account_snapshot.positions`` (a ``{symbol: PortfolioPosition}`` dict,
    the same shape ``execution.queue_builder`` reads via ``getattr``). Prefers the
    position's ``market_value``; falls back to ``quantity * current_price`` when
    that field is absent. Never fabricates a value — an unheld name, a missing
    position, or unparseable fields all yield ``0.0`` (CONSTRAINT #4/#6).
    """
    positions = getattr(account_snapshot, "positions", None) or {}
    if not isinstance(positions, dict):
        return 0.0
    pos = positions.get(symbol) or positions.get(symbol.upper())
    if pos is None:
        return 0.0
    mv = _coerce_float(getattr(pos, "market_value", None))
    if mv is not None and mv > 0:
        return mv
    qty = _coerce_float(getattr(pos, "quantity", None))
    price = _coerce_float(getattr(pos, "current_price", None))
    if qty is not None and price is not None and qty > 0 and price > 0:
        return qty * price
    return 0.0


# ---------------------------------------------------------------------------
# Public API — intent building
# ---------------------------------------------------------------------------

def build_follow_intents(
    pilot: Pilot,
    amount: float,
    account_snapshot: Any,
    snapshot: Optional[dict] = None,
    top_n: Optional[int] = None,
) -> List[FollowIntent]:
    """Build proportional target-notional BUY intents to mirror ``pilot``.

    Parameters
    ----------
    pilot:
        The Pilot to follow.
    amount:
        Total USD the operator wants to allocate across the Pilot's holdings.
    account_snapshot:
        The Robinhood ``AccountSnapshot`` (needs ``.total_equity`` and, for the
        rebalance netting, ``.positions``); used both to translate each order
        notional into a ``suggested_position_pct`` the queue builder can turn back
        into the same notional AND to read the follower's current market value per
        name so the order is sized to the delta vs. the Pilot target.
    snapshot:
        Optional pre-loaded ``output/state_snapshot.json`` dict. When ``None`` it
        is loaded via :func:`pilots.scoring.load_snapshot`.
    top_n:
        Optional override for the number of holdings (defaults to the Pilot's
        normal ``settings.PILOTS_TOP_N`` cap inside ``pilot_holdings``).

    Returns
    -------
    list[FollowIntent]
        One ``FollowIntent`` per Pilot holding that is outside the no-trade band:
        a **BUY** (underweight vs. target) or a **SELL** partial trim (overweight),
        ``target_notional`` = the |delta| clamped by the per-order cap. Holdings
        already within ``_REBALANCE_BAND_FRACTION`` of target are omitted. Empty on
        any failure, on a non-positive ``amount``, on a non-positive account equity,
        or when the Pilot has no holdings. Never raises (CONSTRAINT #6). Pure —
        writes nothing.
    """
    try:
        amt = _coerce_float(amount)
        if amt is None or amt <= 0:
            logger.debug("mirror: non-positive amount (%r); no intents", amount)
            return []

        equity = _coerce_float(getattr(account_snapshot, "total_equity", None))
        if equity is None or equity <= 0:
            logger.info(
                "mirror: account snapshot has no positive total_equity; cannot "
                "build proportional follow intents for pilot %s",
                getattr(pilot, "id", "?"),
            )
            return []

        if snapshot is None:
            from pilots.scoring import load_snapshot
            snapshot = load_snapshot()
        if not isinstance(snapshot, dict):
            logger.info("mirror: no usable state snapshot; no follow intents.")
            return []

        holdings = pilot_holdings(pilot, snapshot, top_n=top_n)
        if not holdings:
            logger.info(
                "mirror: pilot %s produced no holdings from the current snapshot.",
                getattr(pilot, "id", "?"),
            )
            return []

        cap = _max_notional_cap()
        strategy_label = f"Follow:{getattr(pilot, 'id', 'unknown')}"

        intents: List[FollowIntent] = []
        for h in holdings:
            symbol = str(h.get("symbol") or "").upper().strip()
            weight = _coerce_float(h.get("weight"))
            if not symbol or weight is None or weight <= 0:
                continue

            target = amt * weight
            if cap is not None:
                target = min(target, cap)
            if target <= 0:
                continue

            # Rebalance to target: net off the follower's CURRENT market value in
            # this name and size the order to the delta. BUY when underweight, SELL
            # (partial trim) when overweight, skip within the no-trade band.
            current = _current_market_value(account_snapshot, symbol)
            delta = target - current
            band = max(_REBALANCE_MIN_DELTA_USD, _REBALANCE_BAND_FRACTION * target)
            if abs(delta) < band:
                continue

            action = "BUY" if delta > 0 else "SELL"
            order_notional = abs(delta)
            if cap is not None:
                order_notional = min(order_notional, cap)
            if order_notional <= 0:
                continue

            pct = order_notional / equity  # equity > 0 guaranteed above
            intents.append(FollowIntent(
                symbol=symbol,
                action=action,
                strategy=strategy_label,
                # Decision D3: honest per-name conviction == normalized weight.
                conviction=float(weight),
                suggested_position_pct=float(pct),
                target_notional=round(float(order_notional), 2),
                weight=float(weight),
                price=_coerce_float(h.get("price")),
                score=_coerce_float(h.get("score")),
                rationale=strategy_label,
            ))
        return intents
    except Exception as exc:  # pragma: no cover - defensive dead-letter
        logger.debug("mirror: build_follow_intents failed for %s: %s",
                     getattr(pilot, "id", "?"), exc)
        return []


# ---------------------------------------------------------------------------
# Public API — gated planning (emit the queue)
# ---------------------------------------------------------------------------

def plan_follow(
    pilot: Pilot,
    amount: float,
    account_snapshot: Any,
    snapshot: Optional[dict] = None,
    *,
    output_dir: Optional[Any] = None,
) -> Dict[str, Any]:
    """Plan a Pilot follow: build intents and emit the gated dry-run queue.

    Builds the proportional BUY intents (:func:`build_follow_intents`), wraps them
    in a ``RunResult``-shaped :class:`FollowRunResult`, and hands them to
    ``execution.queue_builder.emit_execution_queue`` with a follow-specific
    ``config`` (``strategy_id=f"follow-{pilot.id}"`` + the low
    ``min_conviction`` floor from Decision D3).

    The emitter itself decides whether anything is written: in ``off`` mode
    (the default) it returns ``None`` and NOTHING is written, but the
    ``planned_intents`` preview is still returned so a caller can show the user
    what a follow *would* do. In ``review`` / ``live`` mode the gated queue is
    written atomically to ``output/execution_queue.json`` (``allow_place`` stays
    structurally ``False`` unless ``live`` AND the risk gate passes AND the kill
    switch is clear AND a notional cap is configured — that logic lives in the
    builder and is reused verbatim).

    Returns
    -------
    dict
        ``{"planned_intents": [...serialisable...], "mode": <resolved mode>,
        "queue_written": bool}``. Never raises (CONSTRAINT #6): a scoring/emit
        failure yields a preview-only / empty result, never a crash.
    """
    mode = _resolve_mode()
    intents: List[FollowIntent] = []
    queue_written = False

    try:
        intents = build_follow_intents(pilot, amount, account_snapshot, snapshot=snapshot)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("mirror: plan_follow intent build failed (%s)", exc)
        intents = []

    planned = [i.to_dict() for i in intents]

    if intents:
        try:
            from execution.queue_builder import emit_execution_queue

            run_result = FollowRunResult(recommendations=intents, snapshot=account_snapshot)
            config = {
                "strategy_id": f"follow-{getattr(pilot, 'id', 'unknown')}",
                "min_conviction": FOLLOW_MIN_CONVICTION,
            }
            kwargs: Dict[str, Any] = {"config": config}
            if output_dir is not None:
                from pathlib import Path
                kwargs["output_dir"] = Path(output_dir)
            written_path = emit_execution_queue(run_result, **kwargs)
            queue_written = written_path is not None
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("mirror: emit_execution_queue failed for %s (%s); "
                           "returning preview only", getattr(pilot, "id", "?"), exc)
            queue_written = False

    return {
        "planned_intents": planned,
        "mode": mode,
        "queue_written": queue_written,
    }

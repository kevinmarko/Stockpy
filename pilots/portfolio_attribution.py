"""pilots/portfolio_attribution.py — proxy attribution of a live account's
P&L to the Pilots that, via a follow, claimed each held position.

This is a **pure function module**: :func:`attribute_portfolio_by_pilot` does
no I/O — the caller (``investyo_mcp_server.py::get_portfolio_by_pilot``)
fetches the account snapshot (``data.historical_store.HistoricalStore
.latest_account_snapshot()``) and every follow row
(``pilots.follows_store.FollowsStore.list_all()``) and passes them in. This
mirrors ``pilots/attribution.py``'s existing "pure function, caller supplies
already-fetched inputs" pattern, kept in a dedicated module because the
concept here (attribution BY PILOT of real account P&L) is distinct from that
module's factor-exposure / correlation-cluster attribution.

Why this is an honest PROXY, not real per-lot P&L (read before touching this
module)
-------------------------------------------------------------------------
There is no table anywhere in this codebase linking an actual filled broker
order to the Pilot that originated it — ``execute_paper_trade``'s
``TransactionsStore.strategy`` column is written only for the PAPER trade
journal, never for real Robinhood fills placed via the ``robinhood-execution``
skill. The best real signal this platform has is
``FollowsStore.get_mirrored(pilot_id)``: the last
``[{"symbol", "weight", "target_notional"}]`` set ``pilots.mirror.plan_follow``
computed and persisted — the SAME data ``pilots/mirror.py``'s own force-exit
logic already uses to decide how much of a symbol to sell when a Pilot drops
it (capped at ``min(last target notional, currently held market value)``).
This module reuses that exact capping formula as its own definition of "how
much of this holding is this Pilot's."

Algorithm
---------
1. **Raw per-follow claim**: for every follow row (active AND cancelled — an
   unfollowed Pilot's residual holdings are exactly what
   ``investyo_mcp_server.py::unfollow_pilot`` promises to keep visible) with a
   non-empty ``mirrored`` set, ``raw_claim[pilot_id][symbol] =
   min(mirrored_target_notional, position.market_value)``.
2. **Overlap normalization**: for a symbol claimed by more than one Pilot,
   ``sum(raw_claim[*][symbol])`` can exceed the position's real
   ``market_value`` (independent claims, not a partition). Every Pilot's raw
   claim for that symbol is scaled by
   ``min(1.0, market_value / sum(raw_claim[*][symbol]))`` so the attributed
   total across Pilots for one symbol never exceeds what is actually held.
   Affected rows are labelled ``overlap_scaled: true`` — never a silently
   fabricated split.
3. **P&L pro-ration**: ``attributed_unrealized_pl = (scaled_claim /
   market_value) * position.unrealized_pl``. This is EXACT, not an extra
   approximation layered on top: Robinhood's own ``average_cost`` is already
   a single blended per-share figure (not FIFO/LIFO lots), so if
   ``f = attributed_value / market_value`` then
   ``f * unrealized_pl == f*market_value - f*quantity*average_cost`` exactly,
   because ``average_cost`` already applies uniformly across every share.
4. **Unattributed bucket**: ``market_value - sum(scaled_claim[*][symbol])``
   per symbol — the "manual trade / no follow claims this" residual, always
   surfaced as its own row, never silently dropped.

Honesty rules (CONSTRAINT #4 / #6), preserved throughout:

* A follow row with no ``mirrored`` field (or an empty one — never planned,
  or a legacy row) contributes nothing and is ABSENT from the ``pilots``
  output list — never zero-filled in.
* A mirrored entry with no parseable positive ``target_notional``, or whose
  symbol is not currently held (no matching position, or a non-positive
  market value), contributes nothing — never a fabricated claim.
* ``attributed_unrealized_pl_pct`` is a market-value-weighted fraction
  (``pl / value``, e.g. ``0.125`` == +12.5%), ``None`` when the attributed
  value is zero — never a divide-by-zero fabrication.
* Never raises. Every early-exit path returns the same empty shape with an
  honest ``reason`` string.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["attribute_portfolio_by_pilot", "ATTRIBUTION_NOTE"]

# Surfaced verbatim in both the tool's markdown banner and its JSON payload
# so the proxy-vs-real distinction can never be missed by a caller reading
# only one of the two.
ATTRIBUTION_NOTE = (
    "Attribution is based on each follow's last target allocation "
    "(pilots.follows_store.FollowsStore.get_mirrored), capped by "
    "currently-held market value and scaled down where multiple Pilots "
    "claim the same symbol. This is NOT per-lot cost-basis P&L tracking "
    "-- Stockpy does not record which Pilot originated a specific "
    "executed order."
)

# Below this, a residual is treated as exactly attributed (float noise from
# the overlap-scaling division), not a genuine unattributed sliver.
_RESIDUAL_EPSILON = 1e-6


def _coerce_float(value: Any) -> Optional[float]:
    """Coerce ``value`` to a finite float, or ``None`` when not possible."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
        return None
    return f


def _field(obj: Any, key: str) -> Any:
    """Duck-typed field read: ``dict.get`` or ``getattr``.

    Matches this codebase's existing ``account_snapshot`` duck-typing
    convention (see ``execution/compose.py::_current_market_value``) so
    callers can pass either a real ``AccountSnapshot``/``PortfolioPosition``
    or a plain dict (tests use both).
    """
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _empty_result(reason: Optional[str] = None, as_of: Any = None) -> Dict[str, Any]:
    return {
        "as_of": as_of,
        "attribution_basis": "proxy",
        "note": ATTRIBUTION_NOTE,
        "pilots": [],
        "unattributed": [],
        "reason": reason,
    }


def attribute_portfolio_by_pilot(
    account_snapshot: Any,
    follows: Optional[List[Dict[str, Any]]],
    pilot_names: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Proxy-attribute a live account's P&L to the Pilots that claimed it.

    Parameters
    ----------
    account_snapshot:
        The ``data.robinhood_portfolio.AccountSnapshot`` (or a duck-typed
        stand-in / dict) read from ``HistoricalStore.latest_account_snapshot()``.
        ``None`` (no snapshot on record) degrades honestly.
    follows:
        Every follow row (``pilots.follows_store.FollowsStore.list_all()`` —
        BOTH active and cancelled rows; a cancelled follow's residual holdings
        still deserve attribution). ``None``/empty degrades honestly.
    pilot_names:
        Optional ``{pilot_id: pilot.name}`` map (e.g. built from
        ``pilots.catalog.list_pilots()``) for display purposes only. A
        pilot_id absent from this map still gets an entry with
        ``pilot_name=None`` — never fabricated.

    Returns
    -------
    dict
        ``{"as_of", "attribution_basis": "proxy", "note", "pilots": [...],
        "unattributed": [...], "reason"}`` — see the module docstring for the
        full algorithm and honesty rules. Never raises (CONSTRAINT #6).
    """
    if account_snapshot is None:
        return _empty_result(reason="no account snapshot on record")

    positions = _field(account_snapshot, "positions")
    fetched_at = _field(account_snapshot, "fetched_at")
    as_of = fetched_at.isoformat() if hasattr(fetched_at, "isoformat") else fetched_at

    if not isinstance(positions, dict) or not positions:
        return _empty_result(reason="no positions on the account snapshot", as_of=as_of)

    # Index each held position's honest market_value / unrealized_pl. A
    # non-positive or unparseable market value is excluded entirely (nothing
    # to attribute, nothing meaningfully "unattributed" either — CONSTRAINT #4).
    mv_by_symbol: Dict[str, float] = {}
    pl_by_symbol: Dict[str, float] = {}
    for sym, pos in positions.items():
        sym_u = str(sym).upper().strip()
        if not sym_u:
            continue
        mv = _coerce_float(_field(pos, "market_value"))
        if mv is None or mv <= 0:
            continue
        pl = _coerce_float(_field(pos, "unrealized_pl"))
        mv_by_symbol[sym_u] = mv
        pl_by_symbol[sym_u] = pl if pl is not None else 0.0

    if not mv_by_symbol:
        return _empty_result(
            reason="no positions with positive market value on the account snapshot",
            as_of=as_of,
        )

    # ---- Step 1: raw per-follow, per-symbol claims -------------------------
    raw_claim: Dict[str, Dict[str, float]] = {}
    pilot_meta: Dict[str, Dict[str, Any]] = {}

    for row in follows or []:
        if not isinstance(row, dict):
            continue
        pilot_id = str(row.get("pilot_id") or "").strip()
        if not pilot_id:
            continue
        mirrored = row.get("mirrored")
        if not isinstance(mirrored, list) or not mirrored:
            # Honest: no attribution recorded for this follow (never planned,
            # or a legacy row) -- absent from the breakdown, never zero-filled.
            continue

        claims: Dict[str, float] = {}
        for m in mirrored:
            if not isinstance(m, dict):
                continue
            sym_u = str(m.get("symbol") or "").upper().strip()
            if not sym_u:
                continue
            target_notional = _coerce_float(m.get("target_notional"))
            if target_notional is None or target_notional <= 0:
                continue
            market_value = mv_by_symbol.get(sym_u)
            if market_value is None:
                # Not currently held (or held at $0) -> nothing to attribute.
                continue
            claims[sym_u] = min(target_notional, market_value)

        if not claims:
            continue

        raw_claim[pilot_id] = claims
        pilot_meta[pilot_id] = {
            "pilot_name": (pilot_names or {}).get(pilot_id),
            "mirrored_updated_at": row.get("mirrored_updated_at"),
        }

    if not raw_claim:
        return _empty_result(
            reason="no follow has an attributable claim on a currently-held position",
            as_of=as_of,
        )

    # ---- Step 2: overlap normalization, per symbol -------------------------
    claim_sum_by_symbol: Dict[str, float] = {}
    for claims in raw_claim.values():
        for sym, val in claims.items():
            claim_sum_by_symbol[sym] = claim_sum_by_symbol.get(sym, 0.0) + val

    scale_by_symbol: Dict[str, float] = {}
    for sym, total_claim in claim_sum_by_symbol.items():
        market_value = mv_by_symbol[sym]
        if total_claim <= 0:
            scale_by_symbol[sym] = 1.0
        else:
            scale_by_symbol[sym] = min(1.0, market_value / total_claim)

    # ---- Step 3: scaled claims + per-symbol/per-pilot P&L -------------------
    scaled_claim: Dict[str, Dict[str, float]] = {}
    attributed_by_symbol: Dict[str, float] = {}
    for pilot_id, claims in raw_claim.items():
        scaled: Dict[str, float] = {}
        for sym, raw_val in claims.items():
            scale = scale_by_symbol.get(sym, 1.0)
            scaled_val = raw_val * scale
            scaled[sym] = scaled_val
            attributed_by_symbol[sym] = attributed_by_symbol.get(sym, 0.0) + scaled_val
        scaled_claim[pilot_id] = scaled

    # ---- Step 4: assemble per-pilot output ----------------------------------
    pilots_out: List[Dict[str, Any]] = []
    for pilot_id in sorted(raw_claim.keys()):
        claims = scaled_claim[pilot_id]
        positions_out: List[Dict[str, Any]] = []
        total_value = 0.0
        total_pl = 0.0
        for sym in sorted(claims.keys()):
            attributed_value = claims[sym]
            market_value = mv_by_symbol[sym]
            pl = pl_by_symbol.get(sym, 0.0)
            fraction = attributed_value / market_value if market_value > 0 else 0.0
            attributed_pl = fraction * pl
            overlap_scaled = scale_by_symbol.get(sym, 1.0) < (1.0 - 1e-9)
            positions_out.append({
                "symbol": sym,
                "attributed_value": round(attributed_value, 2),
                "attributed_unrealized_pl": round(attributed_pl, 2),
                "overlap_scaled": overlap_scaled,
            })
            total_value += attributed_value
            total_pl += attributed_pl

        meta = pilot_meta.get(pilot_id, {})
        pilots_out.append({
            "pilot_id": pilot_id,
            "pilot_name": meta.get("pilot_name"),
            "attributed_market_value": round(total_value, 2),
            "attributed_unrealized_pl": round(total_pl, 2),
            "attributed_unrealized_pl_pct": (
                (total_pl / total_value) if total_value > 0 else None
            ),
            "positions": positions_out,
            "mirrored_updated_at": meta.get("mirrored_updated_at"),
        })

    # ---- Step 5: unattributed bucket ----------------------------------------
    unattributed: List[Dict[str, Any]] = []
    for sym in sorted(mv_by_symbol.keys()):
        market_value = mv_by_symbol[sym]
        attributed = attributed_by_symbol.get(sym, 0.0)
        residual = market_value - attributed
        if residual > _RESIDUAL_EPSILON:
            unattributed.append({"symbol": sym, "value": round(residual, 2)})

    return {
        "as_of": as_of,
        "attribution_basis": "proxy",
        "note": ATTRIBUTION_NOTE,
        "pilots": pilots_out,
        "unattributed": unattributed,
        "reason": None,
    }

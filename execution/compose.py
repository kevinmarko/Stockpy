"""execution/compose.py
=========================
Cross-Pilot + advisory queue COMPOSER — the single writer of
``output/execution_queue.json``.

Why this exists
----------------
Two writers already share ``execution_queue.json``: ``main.py`` (the advisory
pipeline, every cycle) and ``pilots.mirror.plan_follow`` (via
``api/pilots_api.py`` / ``investyo_mcp_server.py``, request-driven). Whichever
writes LAST simply overwrites whatever the other wrote — a follow already
clobbers the advisory queue today, latent only because the default
``ROBINHOOD_EXECUTION_MODE="off"`` makes the follow path write nothing. This
module fixes that by becoming the ONE writer: every source writes its own
small JSON file (``output/queue_sources/<source_id>.json``), and this module
UNIONS them, NETS overlapping claims on the same symbol, GATES the result
through the existing risk pipeline, and EMITS one queue via
``execution.queue_builder.emit_execution_queue`` — unchanged, zero new order
-submission code (this file is added to the AST guard's manual scan-target
list precisely because it computes order sizes; see
``tests/test_pipeline_smoke.py::TestNoOrderFunctions._EXECUTION_ZONE_GUARDED_FILES``).

Per-source file schema (``output/queue_sources/<source_id>.json``)
--------------------------------------------------------------------
::

    {
      "schema_version": 1,
      "source_id": "advisory" | "follow-<pilot_id>",
      "generated_at": "<ISO-8601 UTC>",
      "targets": [...],
      "dropped_targets": [...]   # follow sources only
    }

``advisory`` targets carry the same fields ``execution.queue_builder._intent_dict``
already reads off a ``Recommendation`` (``symbol``, ``action``, ``conviction``,
``suggested_position_pct``, ``strategy``, ``rationale``) — a RAW, unfiltered
record of every actionable advisory recommendation that cycle (conviction
filtering happens at compose time, not write time, so a later config change
is honored without needing to rewrite the source).

``follow-<pilot_id>`` targets are ``pilots.mirror.build_follow_targets``'s pure
output (``symbol``, ``weight``, ``target_notional``, ``score``, ``price``,
``rationale``) — a TARGET POSITION SIZE, never an already-netted order delta
(see "net targets, not deltas" below). ``dropped_targets`` are prior-mirrored
rows (``FollowsStore.get_mirrored``, read BEFORE this same call updates it)
whose symbol is no longer in ``targets`` — i.e. names the Pilot has just
dropped that this follow may still need to force-exit.

Composition rules
------------------
1. **Advisory always wins outright.** Whenever a symbol has an advisory claim
   (BUY or SELL, above ``queue_builder.CONFIG["min_conviction"]``), advisory's
   OWN recommendation is emitted verbatim — byte-identical sizing to today's
   advisory-alone path (no netting with any follow claim on that symbol,
   regardless of direction). Any follow claim(s) on that same symbol are
   recorded in the emitted intent's ``overridden`` field (source id, its own
   target notional, its own rationale) for operator visibility — "why should
   I sell/buy this" always shows the real reasoning on both sides — but never
   change the numbers. This is a deliberate product decision (confirmed with
   the operator): advisory is the platform's own risk-calibrated opinion, a
   follow is a preference layered on top; summing them risks stacking two
   independent signals into an oversized position, which is never the safe
   direction for money-sizing code.

2. **Net TARGETS across follow sources, not already-netted deltas.** Two
   Pilots wanting $3750 / $2000 of a symbol the follower holds $6000 of nets
   to ``net_target=5750``, ``delta=-250`` (a $250 trim) — summing the two
   Pilots' OWN already-netted deltas would instead compute
   ``(3750-6000)+(2000-6000)=-6250`` (sell $6250 of a $6000 position, a
   sign-magnitude catastrophe). Each follow source therefore carries its PURE
   target (``pilots.mirror.build_follow_targets``, no account-snapshot
   involvement); netting against actual current holdings happens exactly
   ONCE here, per symbol, across the union of every source claiming it.

3. **Force-exit under the union, capped by attribution.** A symbol dropped by
   every CURRENT-claiming source but still held is force-exited, capped by
   ``source_claim`` = the sum, across every follow source, of whichever of
   its own current-target or dropped-target row exists for that symbol (a
   source can never appear in both — ``dropped_targets`` is by construction
   the set-difference of a prior claim against the current one). This is
   what prevents a follow's force-exit from selling the operator's own
   (non-follow-attributed) shares in the same name.

4. **The no-trade band scales with the NET target**, not any one source's:
   ``band = max($1, 5% * net_target)``. When ``net_target == 0`` (a full
   drop) the band is exactly $1 — a 5% band must never suppress a genuine
   exit just because the target base it would be a percentage of is zero.

Honesty / dead-letter posture (CONSTRAINT #4 / #6)
-----------------------------------------------------
* A MISSING source file is a legitimate, common state (a Pilot that was
  never explicitly followed via ``plan_follow`` yet) — that source is simply
  excluded from the union, and composition proceeds with whatever is
  present.
* A CORRUPT (unparseable) or STALE (``generated_at`` older than
  ``settings.QUEUE_SOURCE_MAX_AGE_SECONDS``) source is a DIFFERENT thing:
  data we cannot trust. Composing without it could, for example, silently
  emit an advisory-only full exit for a name a follow still genuinely wants
  — an order the operator never asked for. The correct response is NOT to
  quietly proceed on a subset of sources; it is to refuse the ENTIRE compose
  call (write nothing, leave the previously-emitted ``execution_queue.json``
  untouched) and log loudly. "Don't crash" (CONSTRAINT #6) is not the same
  promise as "proceed with best-effort partial data" — for order-adjacent
  code, silently proceeding on wrong data is the worse failure mode.
* This module never contacts a broker and defines no order-submission
  function (see the AST guard note above).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "SOURCE_SCHEMA_VERSION",
    "ADVISORY_SOURCE_ID",
    "follow_source_id",
    "SourceReadResult",
    "read_source",
    "write_source",
    "write_advisory_source",
    "write_follow_source",
    "AdvisorySourceClaims",
    "FollowSourceClaims",
    "ComposedIntent",
    "compose_targets",
    "compose_and_emit",
]

SOURCE_SCHEMA_VERSION = 1
ADVISORY_SOURCE_ID = "advisory"
_SOURCE_DIR_NAME = "queue_sources"

# Rebalance no-trade band -- mirrors pilots.mirror's constants exactly (kept
# in lockstep intentionally; this module owns the netting math post-refactor,
# pilots.mirror's own constants are no longer consulted for this).
_REBALANCE_MIN_DELTA_USD = 1.0
_REBALANCE_BAND_FRACTION = 0.05


def follow_source_id(pilot_id: str) -> str:
    return f"follow-{pilot_id}"


def _pilot_id_of(source_id: str) -> str:
    return source_id[len("follow-"):] if source_id.startswith("follow-") else source_id


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


def _current_market_value(account_snapshot: Any, symbol: str) -> float:
    """Follower's CURRENT market value (USD) in ``symbol``, or ``0.0``.

    Duplicated from ``pilots.mirror._current_market_value`` (same tiny
    contract) rather than imported, so this module's core netting math has
    no import-time dependency on ``pilots.*`` — only the source-writing
    helpers (``write_follow_source``) and ``compose_and_emit``'s
    ``FollowsStore`` enumeration touch the Pilot layer, and both do so via
    lazy, function-local imports.
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


def _max_notional_cap() -> Optional[float]:
    """Per-order notional cap in USD, or ``None`` when unset. Same contract
    as ``pilots.mirror._max_notional_cap`` / ``queue_builder._max_notional``."""
    try:
        from settings import settings
        cap = _coerce_float(getattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 0.0))
        if cap is not None and cap > 0:
            return cap
        return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("compose: could not read ROBINHOOD_MAX_NOTIONAL_PER_ORDER (%s)", exc)
        return None


def _source_dir(output_dir: Optional[Any]) -> Path:
    if output_dir is None:
        from settings import settings
        output_dir = settings.OUTPUT_DIR
    return Path(output_dir) / _SOURCE_DIR_NAME


def _source_path(output_dir: Optional[Any], source_id: str) -> Path:
    return _source_dir(output_dir) / f"{source_id}.json"


# ---------------------------------------------------------------------------
# Per-source file I/O
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceReadResult:
    """Result of :func:`read_source`.

    ``present`` false + ``corrupt`` false + ``stale`` false means "this
    source legitimately doesn't exist yet" (e.g. a Pilot never followed) --
    the honest, common, non-error case. ``corrupt`` or ``stale`` true means
    "data exists but cannot be trusted"; callers MUST refuse to compose
    rather than silently proceeding on a subset (see module docstring).
    """

    source_id: str
    present: bool
    corrupt: bool
    stale: bool
    generated_at: Optional[datetime]
    targets: List[Dict[str, Any]]
    dropped_targets: List[Dict[str, Any]]


def read_source(
    source_id: str,
    *,
    output_dir: Optional[Any] = None,
    max_age_seconds: Optional[float] = None,
    now: Optional[datetime] = None,
) -> SourceReadResult:
    """Read one ``queue_sources/<source_id>.json`` file.

    Never raises. A missing file yields ``present=False`` (not an error).
    An unparseable file, or one whose top-level shape is wrong, yields
    ``corrupt=True``. A parseable-but-too-old file yields ``stale=True``
    when ``max_age_seconds`` is given and exceeded.
    """
    now = now or datetime.now(timezone.utc)
    path = _source_path(output_dir, source_id)
    empty = SourceReadResult(
        source_id=source_id, present=False, corrupt=False, stale=False,
        generated_at=None, targets=[], dropped_targets=[],
    )
    if not path.exists():
        return empty
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("compose: %s is unreadable/corrupt (%s)", path, exc)
        return SourceReadResult(
            source_id=source_id, present=True, corrupt=True, stale=False,
            generated_at=None, targets=[], dropped_targets=[],
        )
    if not isinstance(raw, dict):
        logger.warning("compose: %s is not a JSON object; treated as corrupt", path)
        return SourceReadResult(
            source_id=source_id, present=True, corrupt=True, stale=False,
            generated_at=None, targets=[], dropped_targets=[],
        )

    targets = raw.get("targets")
    if not isinstance(targets, list):
        targets = []
    dropped = raw.get("dropped_targets")
    if not isinstance(dropped, list):
        dropped = []

    generated_at: Optional[datetime] = None
    gen_raw = raw.get("generated_at")
    if isinstance(gen_raw, str):
        try:
            generated_at = datetime.fromisoformat(gen_raw)
        except ValueError:
            logger.warning("compose: %s has an unparseable generated_at (%r); treated as corrupt",
                           path, gen_raw)
            return SourceReadResult(
                source_id=source_id, present=True, corrupt=True, stale=False,
                generated_at=None, targets=[], dropped_targets=[],
            )
    if generated_at is None:
        logger.warning("compose: %s is missing generated_at; treated as corrupt", path)
        return SourceReadResult(
            source_id=source_id, present=True, corrupt=True, stale=False,
            generated_at=None, targets=[], dropped_targets=[],
        )

    stale = False
    if max_age_seconds is not None and max_age_seconds > 0:
        age = (now - generated_at).total_seconds()
        stale = age > max_age_seconds

    return SourceReadResult(
        source_id=source_id, present=True, corrupt=False, stale=stale,
        generated_at=generated_at,
        targets=[t for t in targets if isinstance(t, dict)],
        dropped_targets=[d for d in dropped if isinstance(d, dict)],
    )


def write_source(
    source_id: str,
    targets: List[Dict[str, Any]],
    *,
    dropped_targets: Optional[List[Dict[str, Any]]] = None,
    output_dir: Optional[Any] = None,
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """Atomically write one ``queue_sources/<source_id>.json`` file
    (write-then-rename, matching ``execution/kill_switch.py``'s idiom).

    Never raises: a write failure is logged and swallowed, returning
    ``None`` (CONSTRAINT #6) -- a source-write failure must not crash the
    caller (``main.py`` / ``plan_follow``), it just means this source stays
    at its previous state (or absent) until the next successful write.
    """
    now = now or datetime.now(timezone.utc)
    try:
        directory = _source_dir(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{source_id}.json"
        payload = {
            "schema_version": SOURCE_SCHEMA_VERSION,
            "source_id": source_id,
            "generated_at": now.isoformat(),
            "targets": targets or [],
            "dropped_targets": dropped_targets or [],
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
        return path
    except Exception as exc:
        logger.warning("compose: failed to write source %s (%s)", source_id, exc)
        return None


def write_advisory_source(
    recommendations: Any,
    *,
    output_dir: Optional[Any] = None,
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """Write the advisory source from ``main.py``'s ``RunResult.recommendations``.

    A RAW, unfiltered record of every actionable (BUY/SELL) recommendation --
    conviction filtering happens at compose time (against the LIVE
    ``queue_builder.CONFIG["min_conviction"]``), not here, so a later config
    change is honored without needing to rewrite this file.
    """
    now = now or datetime.now(timezone.utc)
    targets: List[Dict[str, Any]] = []
    for rec in recommendations or []:
        try:
            action = str(getattr(rec, "action", "")).upper()
            symbol = str(getattr(rec, "symbol", "")).upper().strip()
            if not symbol or action not in ("BUY", "SELL"):
                continue
            targets.append({
                "symbol": symbol,
                "action": action,
                "conviction": _coerce_float(getattr(rec, "conviction", 0.0)) or 0.0,
                "suggested_position_pct": _coerce_float(getattr(rec, "suggested_position_pct", 0.0)) or 0.0,
                "strategy": str(getattr(rec, "strategy", "")),
                "rationale": str(getattr(rec, "rationale", "") or getattr(rec, "strategy", "")),
            })
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("compose: skipping advisory rec for %s (%s)", getattr(rec, "symbol", "?"), exc)
    return write_source(ADVISORY_SOURCE_ID, targets, output_dir=output_dir, now=now)


def write_follow_source(
    pilot: Any,
    amount: float,
    snapshot: Optional[dict],
    *,
    prior_mirrored: Optional[List[Dict[str, Any]]] = None,
    output_dir: Optional[Any] = None,
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """Write one follow's source file: its pure current targets
    (:func:`pilots.mirror.build_follow_targets`) plus any ``prior_mirrored``
    row whose symbol is no longer in the current targets (a just-detected
    drop this follow may still need to force-exit).

    ``prior_mirrored`` should be read (by the caller, ``plan_follow``)
    BEFORE ``FollowsStore.set_mirrored`` is called for this same cycle --
    otherwise "prior" and "current" would be the same, already-updated
    state and no drop could ever be detected here.
    """
    now = now or datetime.now(timezone.utc)
    pilot_id = str(getattr(pilot, "id", "unknown"))
    from pilots.mirror import build_follow_targets  # lazy: avoid a module-load cycle

    targets = build_follow_targets(pilot, amount, snapshot)
    target_symbols = {t["symbol"] for t in targets}
    dropped: List[Dict[str, Any]] = []
    for row in (prior_mirrored or []):
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").upper().strip()
        if not sym or sym in target_symbols:
            continue
        tn = _coerce_float(row.get("target_notional"))
        if tn is None or tn <= 0:
            continue
        dropped.append({"symbol": sym, "target_notional": round(float(tn), 2)})

    return write_source(
        follow_source_id(pilot_id), targets,
        dropped_targets=dropped, output_dir=output_dir, now=now,
    )


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdvisorySourceClaims:
    targets: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class FollowSourceClaims:
    source_id: str  # "follow-<pilot_id>"
    targets: List[Dict[str, Any]] = field(default_factory=list)
    dropped_targets: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ComposedIntent:
    """The composer's own lightweight, ``getattr``-readable rec shape --
    everything ``execution.queue_builder._intent_dict`` reads off a
    ``Recommendation`` today, plus the two new additive fields (``sources``,
    ``overridden``) it now also reads."""

    symbol: str
    action: str
    strategy: str
    conviction: float
    suggested_position_pct: float
    target_notional: float
    weight: float
    price: Optional[float]
    score: Optional[float]
    rationale: str
    strategy_id: str
    sources: List[Dict[str, Any]] = field(default_factory=list)
    overridden: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class _ComposedRunResult:
    """``RunResult``-shaped shim -- ``execution.queue_builder`` reads only
    ``.recommendations`` and ``.snapshot`` via ``getattr``."""

    recommendations: List[ComposedIntent] = field(default_factory=list)
    snapshot: Any = None


def _advisory_min_conviction() -> float:
    try:
        from execution.queue_builder import CONFIG as _QB_CONFIG
        return float(_QB_CONFIG.get("min_conviction", 0.85))
    except Exception:  # pragma: no cover - defensive
        return 0.85


def _advisory_intent(rec: Dict[str, Any], equity: float) -> Optional[ComposedIntent]:
    symbol = str(rec.get("symbol") or "").upper().strip()
    action = str(rec.get("action") or "").upper()
    if not symbol or action not in ("BUY", "SELL"):
        return None
    conviction = _coerce_float(rec.get("conviction")) or 0.0
    suggested_pct = _coerce_float(rec.get("suggested_position_pct")) or 0.0
    strategy = str(rec.get("strategy") or "")
    rationale = str(rec.get("rationale") or strategy)
    # target_notional here is INFORMATIONAL ONLY (for the `sources` metadata)
    # -- the actual order sizing is recomputed downstream by
    # queue_builder._intent_dict from action/suggested_position_pct exactly
    # as it always has for advisory-alone; the composer never touches it.
    own_notional = round(float(suggested_pct) * equity, 2) if action == "BUY" else 0.0
    return ComposedIntent(
        symbol=symbol, action=action, strategy=strategy, conviction=conviction,
        suggested_position_pct=suggested_pct, target_notional=own_notional,
        weight=0.0, price=None, score=None, rationale=rationale,
        strategy_id=ADVISORY_SOURCE_ID,
        sources=[{"source_id": ADVISORY_SOURCE_ID, "target_notional": own_notional}],
    )


def _build_overridden(
    current_rows: List[Tuple[str, Dict[str, Any]]],
    dropped_rows: List[Tuple[str, Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for sid, row in current_rows:
        out.append({
            "source_id": sid,
            "target_notional": round(float(_coerce_float(row.get("target_notional")) or 0.0), 2),
            "rationale": str(row.get("rationale") or ""),
        })
    for sid, row in dropped_rows:
        out.append({
            "source_id": sid,
            "target_notional": round(float(_coerce_float(row.get("target_notional")) or 0.0), 2),
            "rationale": (
                f"{sid} previously attributed this name (now dropped from its "
                f"current targets)"
            ),
        })
    return out


def _netted_rationale(current_rows: List[Tuple[str, Dict[str, Any]]]) -> str:
    if len(current_rows) == 1:
        # Single-source: byte-identical to that source's own rationale.
        return str(current_rows[0][1].get("rationale") or "")
    parts = []
    for sid, row in current_rows:
        tn = _coerce_float(row.get("target_notional")) or 0.0
        parts.append(str(row.get("rationale") or f"{sid}: ${tn:,.0f} target"))
    return "Composed: " + " | ".join(parts)


def _dropped_rationale(dropped_rows: List[Tuple[str, Dict[str, Any]]], sell_notional: float) -> str:
    if len(dropped_rows) == 1:
        sid, _row = dropped_rows[0]
        pilot_id = _pilot_id_of(sid)
        return (
            f"Follow:{pilot_id} — exit: this name is no longer in the Pilot's "
            f"ranked holdings; trimming the follow-attributed ${sell_notional:,.0f} back out."
        )
    names = ", ".join(_pilot_id_of(sid) for sid, _ in dropped_rows)
    return (
        f"Composed exit: dropped by {names}; trimming the combined "
        f"follow-attributed ${sell_notional:,.0f} back out."
    )


def _build_composed_intent(
    *,
    symbol: str,
    action: str,
    order_notional: float,
    equity: float,
    current_rows: List[Tuple[str, Dict[str, Any]]],
    dropped_rows: List[Tuple[str, Dict[str, Any]]],
    conviction: float,
    weight: float,
    price: Optional[float],
    score: Optional[float],
    rationale: str,
) -> ComposedIntent:
    contributing = [
        (sid, _coerce_float(row.get("target_notional")) or 0.0) for sid, row in current_rows
    ] + [
        (sid, _coerce_float(row.get("target_notional")) or 0.0) for sid, row in dropped_rows
    ]
    distinct_source_ids = sorted({sid for sid, _ in contributing})
    if len(distinct_source_ids) == 1:
        strategy_id = distinct_source_ids[0]
        strategy_label = f"Follow:{_pilot_id_of(strategy_id)}"
    else:
        strategy_id = "composed"
        strategy_label = "Composed: " + ", ".join(
            f"{_pilot_id_of(sid)}(${tn:,.0f})" for sid, tn in contributing
        )
    pct = order_notional / equity
    return ComposedIntent(
        symbol=symbol, action=action, strategy=strategy_label,
        conviction=round(float(conviction), 6), suggested_position_pct=float(pct),
        target_notional=round(float(order_notional), 2), weight=round(float(weight), 6),
        price=price, score=score, rationale=rationale, strategy_id=strategy_id,
        sources=[{"source_id": sid, "target_notional": round(float(tn), 2)} for sid, tn in contributing],
    )


def _follow_netted_intent(
    *,
    symbol: str,
    current_rows: List[Tuple[str, Dict[str, Any]]],
    dropped_rows: List[Tuple[str, Dict[str, Any]]],
    account_snapshot: Any,
    equity: float,
    cap: Optional[float],
) -> Optional[ComposedIntent]:
    net_target = sum((_coerce_float(row.get("target_notional")) or 0.0) for _, row in current_rows)
    source_claim = net_target + sum(
        (_coerce_float(row.get("target_notional")) or 0.0) for _, row in dropped_rows
    )
    current_held = _current_market_value(account_snapshot, symbol)

    if net_target <= 0:
        # Fully dropped by every current-claiming source (or never claimed
        # at all, in which case source_claim is also 0 and nothing is sold).
        if current_held <= 0 or source_claim <= 0:
            return None
        sell_notional = min(current_held, source_claim)
        if cap is not None:
            sell_notional = min(sell_notional, cap)
        if sell_notional <= 0:
            return None
        return _build_composed_intent(
            symbol=symbol, action="SELL", order_notional=sell_notional, equity=equity,
            current_rows=[], dropped_rows=dropped_rows, conviction=0.0, weight=0.0,
            price=None, score=None, rationale=_dropped_rationale(dropped_rows, sell_notional),
        )

    delta = net_target - current_held
    band = max(_REBALANCE_MIN_DELTA_USD, _REBALANCE_BAND_FRACTION * net_target)
    if abs(delta) < band:
        return None

    if delta > 0:
        order_notional = delta
        if cap is not None:
            order_notional = min(order_notional, cap)
        if order_notional <= 0:
            return None
        action = "BUY"
        rows_for_attribution = current_rows
    else:
        order_notional = min(abs(delta), source_claim)
        if cap is not None:
            order_notional = min(order_notional, cap)
        if order_notional <= 0:
            return None
        action = "SELL"
        rows_for_attribution = current_rows

    total_notional = sum((_coerce_float(row.get("target_notional")) or 0.0) for _, row in current_rows) or 1.0
    weight = sum(
        (_coerce_float(row.get("weight")) or 0.0) * ((_coerce_float(row.get("target_notional")) or 0.0) / total_notional)
        for _, row in current_rows
    )
    price = current_rows[0][1].get("price") if len(current_rows) == 1 else None
    score = current_rows[0][1].get("score") if len(current_rows) == 1 else None

    return _build_composed_intent(
        symbol=symbol, action=action, order_notional=order_notional, equity=equity,
        current_rows=rows_for_attribution, dropped_rows=[], conviction=weight, weight=weight,
        price=price, score=score, rationale=_netted_rationale(current_rows),
    )


def compose_targets(
    *,
    advisory: Optional[AdvisorySourceClaims],
    follows: List[FollowSourceClaims],
    account_snapshot: Any,
) -> List[ComposedIntent]:
    """The core per-symbol union → net → conflict-resolve step. Pure (no I/O,
    no gating) -- see :func:`compose_and_emit` for the full pipeline.

    Returns ``[]`` when the account snapshot has no positive ``total_equity``
    (mirrors ``pilots.mirror.build_follow_intents``'s existing guard).
    """
    equity = _coerce_float(getattr(account_snapshot, "total_equity", None))
    if equity is None or equity <= 0:
        return []

    min_conviction = _advisory_min_conviction()
    advisory_claims: Dict[str, Dict[str, Any]] = {}
    for t in (advisory.targets if advisory is not None else []):
        try:
            symbol = str(t.get("symbol") or "").upper().strip()
            action = str(t.get("action") or "").upper()
            if not symbol or action not in ("BUY", "SELL"):
                continue
            conviction = _coerce_float(t.get("conviction")) or 0.0
            if conviction < min_conviction:
                continue
            if symbol in advisory_claims:
                continue  # first one wins; duplicates within one cycle shouldn't happen
            advisory_claims[symbol] = t
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("compose: skipping malformed advisory target (%s)", exc)

    follow_targets_by_symbol: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    follow_dropped_by_symbol: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for f in follows:
        for t in f.targets:
            sym = str(t.get("symbol") or "").upper().strip()
            if not sym:
                continue
            follow_targets_by_symbol.setdefault(sym, []).append((f.source_id, t))
        for d in f.dropped_targets:
            sym = str(d.get("symbol") or "").upper().strip()
            if not sym:
                continue
            follow_dropped_by_symbol.setdefault(sym, []).append((f.source_id, d))

    all_symbols = set(advisory_claims) | set(follow_targets_by_symbol) | set(follow_dropped_by_symbol)
    cap = _max_notional_cap()

    results: List[ComposedIntent] = []
    for symbol in sorted(all_symbols):
        current_rows = follow_targets_by_symbol.get(symbol, [])
        dropped_rows = follow_dropped_by_symbol.get(symbol, [])

        if symbol in advisory_claims:
            ci = _advisory_intent(advisory_claims[symbol], equity)
            if ci is None:
                continue
            if current_rows or dropped_rows:
                ci.overridden = _build_overridden(current_rows, dropped_rows)
            results.append(ci)
            continue

        ci = _follow_netted_intent(
            symbol=symbol, current_rows=current_rows, dropped_rows=dropped_rows,
            account_snapshot=account_snapshot, equity=equity, cap=cap,
        )
        if ci is not None:
            results.append(ci)

    return results


def compose_and_emit(
    account_snapshot: Any,
    *,
    output_dir: Optional[Any] = None,
    mode: Optional[str] = None,
    now: Optional[datetime] = None,
    max_age_seconds: Optional[float] = None,
    extra_follow_pilot_ids: Optional[List[str]] = None,
) -> Optional[Path]:
    """Read every current source, compose, gate, and emit ONE
    ``execution_queue.json`` — the single entry point every writer
    (``main.py``, ``pilots.mirror.plan_follow``) should call after writing
    its own source file.

    ``extra_follow_pilot_ids`` FORCES those pilot ids' source files to be
    considered even if ``FollowsStore.list_active()`` doesn't (yet) list
    them as active. ``pilots.mirror.plan_follow`` always passes its own
    pilot id here: the endpoints that call ``plan_follow``
    (``api/pilots_api.py``, ``investyo_mcp_server.py``) call
    ``FollowsStore.upsert()`` BEFORE ``plan_follow`` in production, so this
    is usually redundant with ``list_active()`` there -- but ``plan_follow``
    is also a valid standalone call (most of ``tests/test_pilots_mirror.py``
    calls it directly, with no separate ``upsert()``), and the pilot a
    caller JUST explicitly followed must never be silently excluded from
    composition merely because a DIFFERENT part of the system hasn't
    recorded it as "active" yet. Deduped against ``list_active()``'s own
    result; a cancelled follow that ISN'T in either set stays correctly
    excluded (its stale source file, if any, is never read).

    Returns the written ``Path``, or ``None`` when: the execution mode is
    ``off`` (nothing written, matching ``emit_execution_queue``'s own
    contract), ANY present source is corrupt or stale (refuses the WHOLE
    compose, leaves the last queue in place — see module docstring), or the
    account snapshot has no positive equity. Never raises (CONSTRAINT #6).
    """
    now = now or datetime.now(timezone.utc)
    if output_dir is None:
        from settings import settings
        output_dir = settings.OUTPUT_DIR
    output_dir = Path(output_dir)

    if max_age_seconds is None:
        try:
            from settings import settings
            max_age_seconds = float(settings.QUEUE_SOURCE_MAX_AGE_SECONDS)
        except Exception:  # pragma: no cover - defensive
            max_age_seconds = 604800.0

    try:
        advisory_read = read_source(
            ADVISORY_SOURCE_ID, output_dir=output_dir,
            max_age_seconds=max_age_seconds, now=now,
        )
        if advisory_read.corrupt or advisory_read.stale:
            logger.warning(
                "compose: refusing to compose -- advisory source corrupt=%s stale=%s "
                "(generated_at=%s); leaving the existing execution_queue.json untouched",
                advisory_read.corrupt, advisory_read.stale, advisory_read.generated_at,
            )
            return None

        try:
            from pilots.follows_store import FollowsStore
            active_pilot_ids = {
                str(f.get("pilot_id")) for f in FollowsStore(
                    path=str(output_dir / "follows.json")
                ).list_active() if f.get("pilot_id")
            }
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("compose: could not enumerate active follows (%s); composing advisory-only", exc)
            active_pilot_ids = set()
        for pid in (extra_follow_pilot_ids or []):
            if pid:
                active_pilot_ids.add(str(pid))
        active_pilot_ids = sorted(active_pilot_ids)

        follow_sources: List[FollowSourceClaims] = []
        for pilot_id in active_pilot_ids:
            sid = follow_source_id(pilot_id)
            r = read_source(sid, output_dir=output_dir, max_age_seconds=max_age_seconds, now=now)
            if not r.present:
                continue  # never explicitly followed via plan_follow yet -- skip, correct.
            if r.corrupt or r.stale:
                logger.warning(
                    "compose: refusing to compose -- follow source %s corrupt=%s stale=%s "
                    "(generated_at=%s); leaving the existing execution_queue.json untouched",
                    sid, r.corrupt, r.stale, r.generated_at,
                )
                return None
            follow_sources.append(FollowSourceClaims(
                source_id=sid, targets=r.targets, dropped_targets=r.dropped_targets,
            ))

        advisory_claims = (
            AdvisorySourceClaims(targets=advisory_read.targets) if advisory_read.present else None
        )
        composed = compose_targets(
            advisory=advisory_claims, follows=follow_sources, account_snapshot=account_snapshot,
        )
        if not composed:
            # Nothing to write across EVERY source, not just one caller's own
            # contribution -- avoid unnecessary queue churn (and matches the
            # pre-composer contract: a follow with nothing to contribute, and
            # no other source with anything either, must not touch the queue
            # file). Genuinely partial results (some symbols, not zero) still
            # proceed normally below.
            return None

        run_result = _ComposedRunResult(recommendations=composed, snapshot=account_snapshot)
        from execution.queue_builder import emit_execution_queue
        return emit_execution_queue(
            run_result, mode=mode, output_dir=output_dir,
            config={"strategy_id": "composed", "min_conviction": 0.0}, now=now,
        )
    except Exception as exc:  # pragma: no cover - belt-and-suspenders dead-letter
        logger.warning("compose: compose_and_emit failed (%s); execution_queue.json untouched", exc)
        return None

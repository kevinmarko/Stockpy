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

    **Rebalance scope — force-exit of dropped names (per-follow attribution):**
    rebalancing covers the Pilot's CURRENT holdings AND names the follow itself
    previously mirrored that the Pilot has since fully dropped. The attribution
    comes from ``FollowsStore``'s persisted *last mirrored holding set* per
    follow (symbol + target weight + target notional): ``plan_follow`` loads that
    set, ``build_follow_intents`` diffs it against the Pilot's current holdings,
    and for each symbol previously mirrored but no longer held by the Pilot it
    emits a **SELL to zero** — but sized to the FOLLOW-ATTRIBUTED quantity only
    (``min(last target notional, currently held market value)``), reusing the
    queue builder's partial-trim signalling (a positive
    ``suggested_position_pct`` capped downstream at the held quantity — see
    ``execution/queue_builder.py::_intent_dict``). It therefore never touches the
    follower's shares beyond what this follow put on, and never oversells.

    Honest bounds that remain: (1) attribution is the last *target* notional, not
    a real per-lot cost basis, so it is a proportional estimate capped by what is
    actually held — never a fabricated position; (2) with NO prior mirrored set
    (a legacy follow, or the very first follow) there is nothing to attribute, so
    the pre-existing behavior holds and NOTHING is force-sold; (3) a name that is
    unrelated to any Pilot the follower ever mirrored is still left untouched;
    (4) attribution for a dropped-but-still-held name is RETAINED across calls,
    not silently discarded the moment it is first computed — see ``plan_follow``'s
    persistence step below for why ``queue_written`` is the wrong axis for this
    and held-ness is the right one.

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
    "build_follow_targets",
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


def _follow_rationale(
    pilot: Any,
    *,
    rank: int,
    total: int,
    score: Optional[float],
    weight: float,
    target_notional: float,
) -> str:
    """Build the honest per-name "why" for a follow intent — a RANKING, not a thesis.

    A Pilot didn't reason about a stock; it ranked the stock's blended signal
    score against its peers and normalized the survivors' scores into target
    weights (see ``pilots.scoring.pilot_holdings``). This one-liner says exactly
    that, in real numbers pulled straight from the holdings row — nothing
    invented (CONSTRAINT #4). It deliberately reads as "ranked #N by score",
    never "the Pilot believes...", so the operator is never misled into thinking
    a discretionary judgment was made. Example::

        Follow:trend-following — ranked #2 of 20 by its signal blend
        (score 0.82 → 25.0% target weight, $2,500 target).

    ``score`` may be ``None`` (an older snapshot without the field); the phrase
    degrades to omit it rather than fabricate one.
    """
    label = f"Follow:{getattr(pilot, 'id', 'unknown')}"
    score_frag = f"score {score:.2f} → " if score is not None else ""
    return (
        f"{label} — ranked #{rank} of {total} by its signal blend "
        f"({score_frag}{weight * 100:.1f}% target weight, "
        f"${target_notional:,.0f} target)."
    )


def build_follow_targets(
    pilot: Pilot,
    amount: float,
    snapshot: Optional[dict],
    top_n: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Pure target-notional math for a Pilot follow — the shared primitive
    both the legacy single-pilot preview path (:func:`build_follow_intents`,
    via ``execution.compose``) and the multi-source composer
    (``execution.compose.compose_and_emit``) build on.

    ``[{"symbol", "weight", "target_notional", "score", "price", "rationale"}]``
    where ``target_notional`` is the follow's TARGET POSITION SIZE in that name
    (``amount * weight``, clamped by the per-order notional cap) — this is what
    ``FollowsStore.set_mirrored`` records so a future follow can attribute (and
    force-exit) a name the Pilot later drops, and what a per-source
    ``queue_sources/follow-<pilot_id>.json`` file carries for composition.

    Deliberately takes NO account snapshot and does NO rebalance-to-current
    netting and decides NO BUY/SELL direction — netting against actual account
    holdings happens exactly ONCE, downstream (never here): summing two
    ALREADY-netted per-Pilot deltas for the same symbol double-counts the same
    held position (the queue-composition design's "net targets, not deltas"
    proof — two Pilots wanting $3750/$2000 of a $6000 holding nets to a $250
    trim, not the ($3750-$6000)+($2000-$6000) = -$6250 that summing deltas
    would produce).

    Empty on a non-positive ``amount``, a missing snapshot, or no Pilot holdings.
    Never raises (CONSTRAINT #6); no fabricated positions (CONSTRAINT #4).
    """
    try:
        amt = _coerce_float(amount)
        if amt is None or amt <= 0 or not isinstance(snapshot, dict):
            return []
        holdings = pilot_holdings(pilot, snapshot, top_n=top_n)
        if not holdings:
            return []
        cap = _max_notional_cap()
        total_holdings = len(holdings)
        out: List[Dict[str, Any]] = []
        for rank0, h in enumerate(holdings):
            symbol = str(h.get("symbol") or "").upper().strip()
            weight = _coerce_float(h.get("weight"))
            if not symbol or weight is None or weight <= 0:
                continue
            target = amt * weight
            if cap is not None:
                target = min(target, cap)
            if target <= 0:
                continue
            score = _coerce_float(h.get("score"))
            out.append({
                "symbol": symbol,
                "weight": round(float(weight), 6),
                "target_notional": round(float(target), 2),
                "score": score,
                "price": _coerce_float(h.get("price")),
                # Bug D-style honest ranking rationale, precomputed here (not
                # at compose time) so a per-source file is a self-contained
                # record of what this Pilot claimed and why, at write time.
                "rationale": _follow_rationale(
                    pilot, rank=rank0 + 1, total=total_holdings,
                    score=score, weight=float(weight), target_notional=float(target),
                ),
            })
        return out
    except Exception as exc:  # pragma: no cover - defensive dead-letter
        logger.debug("mirror: build_follow_targets failed for %s: %s",
                     getattr(pilot, "id", "?"), exc)
        return []


# ---------------------------------------------------------------------------
# Public API — intent building
# ---------------------------------------------------------------------------

def build_follow_intents(
    pilot: Pilot,
    amount: float,
    account_snapshot: Any,
    snapshot: Optional[dict] = None,
    top_n: Optional[int] = None,
    prior_mirrored: Optional[List[Dict[str, Any]]] = None,
) -> List[FollowIntent]:
    """Build proportional target-notional rebalance intents to mirror ``pilot``.

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
    prior_mirrored:
        Optional last-mirrored holding set for THIS follow
        (``[{"symbol", "weight", "target_notional"}]`` from
        ``FollowsStore.get_mirrored``). Enables force-exit of names the Pilot has
        since fully dropped: any symbol in this set that is no longer a current
        Pilot holding gets a **SELL to zero** intent sized to the follow-attributed
        quantity only (``min(last target notional, currently held market value)``),
        capped downstream at the held quantity. ``None``/empty (a legacy or
        first-ever follow) → no force-exit, byte-identical to the pre-attribution
        behavior. Callers must pass the FOLLOW's own prior set — never a shared one.

    Returns
    -------
    list[FollowIntent]
        One ``FollowIntent`` per Pilot holding that is outside the no-trade band:
        a **BUY** (underweight vs. target) or a **SELL** partial trim (overweight),
        ``target_notional`` = the |delta| clamped by the per-order cap. Holdings
        already within the no-trade band of target are omitted. Plus, when
        ``prior_mirrored`` is supplied, one **SELL** force-exit per dropped name the
        follow still holds. Empty on any failure, on a non-positive ``amount``, on a
        non-positive account equity, or when the Pilot has no holdings AND there is
        nothing to force-exit. Never raises (CONSTRAINT #6). Pure — writes nothing.

    Implementation note (queue-composition refactor): this is now a thin
    single-source wrapper over ``execution.compose``'s shared netting engine —
    the SAME per-symbol "net targets, then net once against current holdings,
    capped by attribution" logic the multi-source composer uses for a follow
    unioned with other Pilots/advisory. Composing exactly one follow source
    and no advisory source reproduces this function's pre-refactor output
    byte-for-byte (the safety net the whole composer refactor leans on — see
    ``tests/test_compose.py``'s single-source byte-identity tests and
    ``tests/test_pilots_mirror.py``'s existing coverage, both unchanged).
    """
    try:
        amt = _coerce_float(amount)
        if amt is None or amt <= 0:
            logger.debug("mirror: non-positive amount (%r); no intents", amount)
            return []

        if snapshot is None:
            from pilots.scoring import load_snapshot
            snapshot = load_snapshot()
        if not isinstance(snapshot, dict):
            logger.info("mirror: no usable state snapshot; no follow intents.")
            return []

        from execution.compose import (
            FollowSourceClaims,
            compose_targets,
            follow_source_id,
        )

        targets = build_follow_targets(pilot, amount, snapshot, top_n=top_n)
        prior = prior_mirrored or []
        if not targets and not prior:
            logger.info(
                "mirror: pilot %s produced no holdings from the current snapshot.",
                getattr(pilot, "id", "?"),
            )
            return []

        target_symbols = {t["symbol"] for t in targets}
        dropped: List[Dict[str, Any]] = []
        for m in prior:
            if not isinstance(m, dict):
                continue
            sym = str(m.get("symbol") or "").upper().strip()
            if not sym or sym in target_symbols:
                continue
            attributed = _coerce_float(m.get("target_notional"))
            if attributed is None or attributed <= 0:
                continue
            dropped.append({"symbol": sym, "target_notional": round(float(attributed), 2)})

        source = FollowSourceClaims(
            source_id=follow_source_id(str(getattr(pilot, "id", "unknown"))),
            targets=targets,
            dropped_targets=dropped,
        )
        composed = compose_targets(advisory=None, follows=[source], account_snapshot=account_snapshot)
        return [
            FollowIntent(
                symbol=ci.symbol,
                action=ci.action,
                strategy=ci.strategy,
                conviction=ci.conviction,
                suggested_position_pct=ci.suggested_position_pct,
                target_notional=ci.target_notional,
                weight=ci.weight,
                price=ci.price,
                score=ci.score,
                rationale=ci.rationale,
            )
            for ci in composed
        ]
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
    follows_store: Optional[Any] = None,
) -> Dict[str, Any]:
    """Plan a Pilot follow: build intents and emit the gated dry-run queue.

    Builds the proportional rebalance intents (:func:`build_follow_intents`),
    wraps them in a ``RunResult``-shaped :class:`FollowRunResult`, and hands them
    to ``execution.queue_builder.emit_execution_queue`` with a follow-specific
    ``config`` (``strategy_id=f"follow-{pilot.id}"`` + the low
    ``min_conviction`` floor from Decision D3).

    **Per-follow attribution (force-exit of dropped names):** the follow's *last
    mirrored holding set* is loaded from ``FollowsStore`` and threaded into
    :func:`build_follow_intents` as ``prior_mirrored`` so a name the Pilot has
    since fully dropped is force-sold (attributed quantity only). After building,
    the Pilot's CURRENT target holdings are persisted back via
    ``FollowsStore.set_mirrored`` — PLUS any row from ``prior_mirrored`` whose
    symbol is no longer a current Pilot holding but the follower STILL holds
    market value in (see the retention note below) — so the next follow can
    attribute the next drop AND does not lose track of a drop it already
    started attributing. The store path follows ``output_dir`` when supplied
    (keeping the follows file beside the queue for isolated tests), else the
    default ``settings.OUTPUT_DIR / "follows.json"`` the API already writes.

    **Retention axis is held-ness, not ``queue_written`` or mode:** an earlier
    version of this persistence step overwrote the mirrored set to exactly the
    Pilot's current holdings on every call, unconditionally. In ``off`` mode
    (the default) ``emit_execution_queue`` always returns ``None`` and writes
    nothing, so a force-exit computed by :func:`build_follow_intents` on one
    call was shown only in that call's ephemeral ``planned_intents`` response
    and then immediately forgotten — the very next ``plan_follow`` call had no
    record it had ever been dropped. Gating on ``queue_written`` instead does
    not fix this either: in ``review`` mode a queue IS written, but the
    ``robinhood-execution`` skill contractually never places from a review
    -mode queue, so "written" still does not mean "acted on". The correct
    signal is whether the follower's account still shows market value in the
    dropped name — a dropped name is retained here (carried forward alongside
    ``current_set``) for as long as the follower still holds it, and drops out
    of the retained set the moment ``_current_market_value`` reads zero,
    however that exit actually happened (placed via the skill, sold manually,
    or exited some other way). Current Pilot holdings always advance
    unconditionally; only the RETAINED (dropped-but-still-held) rows get this
    extra held-ness check.

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
    pilot_id = str(getattr(pilot, "id", "unknown"))

    # Load the snapshot once so both the intent build and the mirrored-set
    # persistence see the same state.
    if snapshot is None:
        try:
            from pilots.scoring import load_snapshot
            snapshot = load_snapshot()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("mirror: plan_follow could not load snapshot (%s)", exc)
            snapshot = None

    # Resolve the follows store (DI for tests). Align its path with output_dir
    # when supplied so a test's queue + follows file share one scratch dir; the
    # API path (no output_dir) uses the same default file it upserts into.
    store = follows_store
    prior_mirrored: List[Dict[str, Any]] = []
    try:
        if store is None:
            from pilots.follows_store import FollowsStore
            if output_dir is not None:
                from pathlib import Path
                store = FollowsStore(path=str(Path(output_dir) / "follows.json"))
            else:
                store = FollowsStore()
        prior_mirrored = store.get_mirrored(pilot_id) or []
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("mirror: plan_follow could not load prior mirrored set for %s (%s)",
                     pilot_id, exc)
        store = None
        prior_mirrored = []

    try:
        intents = build_follow_intents(
            pilot, amount, account_snapshot,
            snapshot=snapshot, prior_mirrored=prior_mirrored,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("mirror: plan_follow intent build failed (%s)", exc)
        intents = []

    planned = [i.to_dict() for i in intents]

    # Write this follow's per-source file (its pure current targets, plus any
    # prior_mirrored row just detected as dropped) and hand off to the
    # cross-Pilot + advisory composer — the SINGLE writer of
    # output/execution_queue.json (execution/compose.py). This REPLACES a
    # direct emit_execution_queue(FollowRunResult(...)) call that used to
    # write (or clobber) the queue from THIS follow alone: two writers
    # already shared that one file (main.py's advisory cycle and this
    # function), and whichever ran last silently overwrote the other. The
    # source write happens even when `intents` (this follow's OWN rebalance
    # preview) is empty — an empty targets list is itself meaningful input
    # to the composer (e.g. this Pilot currently holds nothing, so any
    # previously-attributed name must be force-exited under the union, not
    # silently skipped because this cycle produced no preview intents).
    # `queue_written` now answers "did the actual execution queue file get
    # written/updated as a result of this action" — which may also reflect
    # OTHER sources' claims, not only this pilot's own, since composition is
    # a union. That is the more honest question for an operator to ask.
    if store is not None:
        try:
            from execution.compose import compose_and_emit, write_follow_source

            write_follow_source(
                pilot, amount, snapshot,
                prior_mirrored=prior_mirrored, output_dir=output_dir,
            )
            written_path = compose_and_emit(
                account_snapshot, output_dir=output_dir,
                extra_follow_pilot_ids=[pilot_id],
            )
            queue_written = written_path is not None
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("mirror: compose_and_emit failed for %s (%s); "
                           "returning preview only", pilot_id, exc)
            queue_written = False

    # Persist the CURRENT target holding set so a future follow can attribute (and
    # force-exit) any name the Pilot drops between now and then. Only when we have
    # a positive amount + usable snapshot: a cancel (amount <= 0) or an
    # unavailable snapshot must NOT wipe the prior attribution.
    #
    # Bug A fix: also carry forward any `prior_mirrored` row for a symbol that
    # dropped out of `current_set` (the Pilot no longer holds it) but the
    # follower's account STILL shows market value in it -- see the module and
    # function docstrings above for why `queue_written`/mode is the wrong axis
    # and held-ness is the right one. A retained row is passed through
    # UNCHANGED (its original weight/target_notional, not recomputed) since it
    # still represents this follow's original claim on that name; it drops out
    # on its own the next time this runs once the held market value hits zero.
    #
    # Deliberately AFTER the write_follow_source/compose_and_emit block above:
    # write_follow_source's own dropped-name detection reads `prior_mirrored`
    # (captured earlier in this function, before any update) -- if this
    # persistence step ran first and overwrote the store, there would be
    # nothing left to detect as "just dropped" on this same call.
    if store is not None:
        try:
            current_set = build_follow_targets(pilot, amount, snapshot)
            if current_set:
                current_symbols = {row.get("symbol") for row in current_set}
                retained: List[Dict[str, Any]] = []
                for row in prior_mirrored:
                    if not isinstance(row, dict):
                        continue
                    sym = str(row.get("symbol") or "").upper().strip()
                    if not sym or sym in current_symbols:
                        continue
                    if _current_market_value(account_snapshot, sym) > 0:
                        retained.append(row)
                store.set_mirrored(pilot_id, current_set + retained)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("mirror: plan_follow could not persist mirrored set for %s (%s)",
                         pilot_id, exc)

    # Emit exactly one pilot-attributed alert for this follow plan. This is the
    # ONLY genuinely pilot-scoped alert in the platform: only follow-planning
    # carries the Pilot's identity. Risk-gate / kill-switch / reconciliation
    # alerts stay PLATFORM-scoped and are deliberately NOT backfilled with a
    # pilot_id — those subsystems have no notion of which Pilot (if any) a given
    # order intent belongs to, so attributing one would be fabricated. The alert
    # is lazily imported and fully dead-lettered (CONSTRAINT #6): an alerting
    # failure must NEVER break plan_follow's normal result.
    try:
        from observability.alerts import send_alert

        send_alert(
            "INFO",
            f"Follow planned for {pilot.name}: {len(intents)} intent(s), mode={mode}",
            extra={
                "type": "follow_planned",
                "pilot_id": pilot.id,
                "amount": amount,
                "intent_count": len(intents),
                "mode": mode,
                "queue_written": queue_written,
            },
        )
    except Exception as exc:  # pragma: no cover - defensive dead-letter
        logger.debug("mirror: plan_follow alert emission failed for %s (%s)",
                     pilot_id, exc)

    return {
        "planned_intents": planned,
        "mode": mode,
        "queue_written": queue_written,
    }

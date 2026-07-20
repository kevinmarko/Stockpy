"""
pilots/symbols.py — symbol-centric readers over the persisted state snapshot.
=============================================================================

The symbol-centric complement to ``pilots.scoring`` (which is pilot-centric:
"derive a Pilot's holdings/sector/trades"). This module answers the inverse
question — "for one ticker, what does the latest snapshot say about it, and
which Pilots hold it, and at what weight" — powering the consumer PWA's symbol
detail pages (``GET /symbols/{ticker}``).

Design invariants (identical to the rest of the Pilots read layer):

* **Read-only / persisted-state only** — reuses ``scoring.load_snapshot`` inputs
  and ``scoring.pilot_holdings``; imports only ``pilots.catalog``,
  ``pilots.scoring``, and ``settings`` (no heavy engines), so
  ``api/pilots_api.py`` stays inside the import-guard allow-list.
* **Honesty (CONSTRAINT #4)** — a field the active snapshot does not carry is
  emitted as ``None``, NEVER a fabricated ``0.0``. Two orchestrator writers exist
  (``main_orchestrator._write_state_snapshot`` = rich;
  ``reporting/state_snapshot.py`` = advisory, fewer per-symbol fields), so every
  numeric leaf is nulled independently via ``scoring._coerce_float`` and every
  string via strip-to-``None``. **Load-bearing subtlety:** the advisory writer
  emits ``price = 0.0`` for a name the account does not currently hold, so a
  non-positive ``price`` is mapped to ``None`` (a ``$0.00`` would be a fabricated
  quote). ``shares == 0.0``, by contrast, is a genuine "you hold none" and is
  kept.
* **Never raises (CONSTRAINT #6)** — every public function degrades to
  ``None`` / ``[]`` on malformed input.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from settings import settings

from pilots import catalog, scoring

logger = logging.getLogger(__name__)

__all__ = [
    "compare_symbols",
    "find_signal",
    "held_by_pilots",
    "list_recommendations",
    "list_universe",
    "symbol_detail",
]

# Mirrors gui/panels/strategy_matrix.py::_render_symbol_comparison's
# `st.multiselect(..., max_selections=3)` hard cap, with a little headroom on
# the API side to match Comparison.tsx's existing Pilot-vs-Pilot selector cap
# (5) — the PWA's own symbol multi-select UI keeps the legacy "2-3
# recommended" guidance, but the endpoint itself doesn't need to hard-fail a
# future 4th/5th symbol.
COMPARE_MIN_SYMBOLS = 2
COMPARE_MAX_SYMBOLS = 5


# ---------------------------------------------------------------------------
# Honesty helpers
# ---------------------------------------------------------------------------

def _clean_str(value: Any) -> Optional[str]:
    """Strip a display string; empty (or ``None``) → ``None`` (CONSTRAINT #4)."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _clean_price(value: Any) -> Optional[float]:
    """Coerce to a finite float, then map non-positive (``<= 0``) → ``None``.

    Stricter than ``_coerce_float`` because the advisory snapshot writer emits
    ``price = 0.0`` for any symbol the account does not currently hold — a ``0.0``
    here is a placeholder, not a real quote, so it must not surface as ``$0.00``.
    """
    f = scoring._coerce_float(value)
    return f if (f is not None and f > 0.0) else None


def _clean_components(value: Any) -> Optional[Dict[str, Any]]:
    """A non-empty per-module score dict, else ``None`` (``{}`` → ``None``)."""
    return value if (isinstance(value, dict) and value) else None


# ---------------------------------------------------------------------------
# Signal lookup
# ---------------------------------------------------------------------------

def find_signal(snapshot: Any, ticker: str) -> Optional[dict]:
    """Return the raw ``signals[]`` entry whose symbol matches *ticker*.

    Case-insensitive and whitespace-stripped on both sides (neither snapshot
    writer uppercases ``symbol``). First match wins. ``None`` on miss, empty
    ticker, or malformed snapshot. Never raises.
    """
    try:
        if not isinstance(snapshot, dict):
            return None
        target = str(ticker or "").upper().strip()
        if not target:
            return None
        signals = snapshot.get("signals") or []
        if not isinstance(signals, list):
            return None
        for sig in signals:
            if not isinstance(sig, dict):
                continue
            if str(sig.get("symbol") or "").upper().strip() == target:
                return sig
        return None
    except Exception as exc:  # noqa: BLE001 — never raises (CONSTRAINT #6)
        logger.debug("find_signal(%s) failed: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Tracked universe (symbol autocomplete source)
# ---------------------------------------------------------------------------

def list_universe(snapshot: Any) -> List[dict]:
    """Return ``[{"symbol", "action"}]`` for every ticker the latest snapshot
    tracks, sorted by symbol.

    The tracked universe is exactly the snapshot's ``signals[]`` (held positions
    ∪ watchlist — the same set every per-symbol read serves), so every returned
    symbol resolves to a real ``GET /symbols/{ticker}`` detail page (no dead-end
    suggestions). ``action`` is the holding-aware ``advisory_action`` when present,
    else the raw signal ``action``, else ``None`` (never fabricated — CONSTRAINT
    #4); it only decorates the autocomplete row. Symbols are upper-cased and
    de-duplicated (first entry wins for ``action``). ``[]`` on a cold start (no
    snapshot) or a malformed snapshot. Never raises (CONSTRAINT #6).
    """
    try:
        if not isinstance(snapshot, dict):
            return []
        signals = snapshot.get("signals") or []
        if not isinstance(signals, list):
            return []
        seen: Dict[str, Optional[str]] = {}
        for sig in signals:
            if not isinstance(sig, dict):
                continue
            symbol = str(sig.get("symbol") or "").upper().strip()
            if not symbol or symbol in seen:
                continue
            action = _clean_str(sig.get("advisory_action")) or _clean_str(sig.get("action"))
            seen[symbol] = action
        return [{"symbol": s, "action": seen[s]} for s in sorted(seen)]
    except Exception as exc:  # noqa: BLE001 — never raises (CONSTRAINT #6)
        logger.debug("list_universe failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Ranked recommendations (the platform's current BUY picks)
# ---------------------------------------------------------------------------

def list_recommendations(snapshot: Any, limit: int = 25) -> List[dict]:
    """Return the latest snapshot's BUY-rated picks, ranked by conviction.

    The sibling to :func:`list_universe`: where that returns *every* tracked
    symbol decorated with its action, this returns only the actionable BUY
    picks — what the advisory engine would buy right now — as a ranked feed for
    the PWA's "Recommended stocks" surface.

    * **Selection** — an entry is kept when its holding-aware ``advisory_action``
      (else the raw signal ``action``) is a BUY-family action (contains
      ``"BUY"`` — covers ``"BUY"`` / ``"STRONG BUY"``). ``HOLD``/``SELL`` and
      un-actioned rows are dropped.
    * **Ranking** — conviction descending, then ``score`` descending, then
      symbol ascending (a stable, deterministic order even when conviction is
      absent for some rows). ``None`` conviction/score sort last within their
      tier.
    * **Honesty (CONSTRAINT #4)** — every numeric leaf (``conviction`` from
      ``advisory_conviction``, ``score``) is nulled via ``scoring._coerce_float``
      when absent, ``price`` via :func:`_clean_price` (non-positive → ``None``),
      and ``buy_range``/``sector``/``action`` via :func:`_clean_str`. Nothing is
      fabricated.
    * ``limit`` is clamped to ``[1, 200]``. ``[]`` on a cold start (no snapshot),
      a malformed snapshot, or when nothing is BUY-rated. **Never raises**
      (CONSTRAINT #6).
    """
    try:
        if not isinstance(snapshot, dict):
            return []
        signals = snapshot.get("signals") or []
        if not isinstance(signals, list):
            return []
        try:
            cap = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            cap = 25
        cf = scoring._coerce_float

        picks: List[dict] = []
        for sig in signals:
            if not isinstance(sig, dict):
                continue
            symbol = str(sig.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            action = _clean_str(sig.get("advisory_action")) or _clean_str(sig.get("action"))
            if not action or "BUY" not in action.upper():
                continue
            picks.append({
                "symbol": symbol,
                "action": action,
                "conviction": cf(sig.get("advisory_conviction")),
                "score": cf(sig.get("score")),
                "buy_range": _clean_str(sig.get("buy_range")),
                "sector": _clean_str(sig.get("sector")),
                "price": _clean_price(sig.get("price")),  # non-positive → None
            })

        # Conviction desc, score desc, symbol asc. None sorts last within a tier
        # (a missing conviction must never outrank a real one) via the -inf key.
        picks.sort(
            key=lambda p: (
                -(p["conviction"] if p["conviction"] is not None else float("-inf")),
                -(p["score"] if p["score"] is not None else float("-inf")),
                p["symbol"],
            )
        )
        return picks[:cap]
    except Exception as exc:  # noqa: BLE001 — never raises (CONSTRAINT #6)
        logger.debug("list_recommendations failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Reverse cross-link: which Pilots hold this symbol
# ---------------------------------------------------------------------------

def held_by_pilots(
    ticker: str,
    snapshot: Any,
    pilots: Optional[List[Any]] = None,
) -> List[dict]:
    """Return ``[{"pilot_id", "name", "weight"}]`` for every Pilot whose
    advertised top-N holdings include *ticker*.

    "Held" means the symbol survives the Pilot's blend into its top-N (the same
    definition the marketplace advertises via ``scoring.pilot_holdings``) — a
    positive-but-below-cap name is honestly NOT counted. ``weight`` is the
    symbol's normalized weight within that Pilot. Sorted weight-descending,
    ``pilot_id``-ascending for determinism. ``[]`` when no Pilot holds it or the
    snapshot is malformed. Never raises.
    """
    try:
        target = str(ticker or "").upper().strip()
        if not target or not isinstance(snapshot, dict):
            return []
        if pilots is None:
            pilots = catalog.list_pilots()
        out: List[dict] = []
        for pilot in pilots:
            try:
                holdings = scoring.pilot_holdings(pilot, snapshot)
            except Exception:  # noqa: BLE001 — one bad Pilot never sinks the list
                continue
            for h in holdings:
                if not isinstance(h, dict):
                    continue
                if str(h.get("symbol") or "").upper().strip() == target:
                    out.append({
                        "pilot_id": pilot.id,
                        "name": pilot.name,
                        "weight": scoring._coerce_float(h.get("weight")),
                    })
                    break  # a symbol appears at most once per Pilot
        out.sort(key=lambda d: (-(d["weight"] or 0.0), d["pilot_id"]))
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("held_by_pilots(%s) failed: %s", ticker, exc)
        return []


# ---------------------------------------------------------------------------
# Grouped symbol detail
# ---------------------------------------------------------------------------

def symbol_detail(
    snapshot: Any,
    ticker: str,
    pilots: Optional[List[Any]] = None,
) -> Optional[dict]:
    """Reshape one ``signals[]`` entry into the grouped symbol-detail payload,
    honestly nulling every field the active snapshot does not carry, and attach
    the reverse cross-link.

    Returns ``None`` iff *ticker* is absent from the snapshot (the endpoint maps
    that to a 404). Never raises.
    """
    try:
        sig = find_signal(snapshot, ticker)
        if sig is None:
            return None
        cf = scoring._coerce_float
        symbol = str(sig.get("symbol") or ticker or "").upper().strip()
        as_of = snapshot.get("timestamp") if isinstance(snapshot, dict) else None
        return {
            "symbol": symbol,
            "as_of": as_of,
            "reason": None,  # honest soft-note slot; None on a normal hit
            "identity": {
                "sector": _clean_str(sig.get("sector")),
                "price": _clean_price(sig.get("price")),  # non-positive → None
                "action": _clean_str(sig.get("action")),  # raw signal action
                "shares": cf(sig.get("shares")),           # 0.0 kept (genuine "hold none")
            },
            "advisory": {
                "action": _clean_str(sig.get("advisory_action")),  # holding-aware overlay
                "conviction": cf(sig.get("advisory_conviction")),
                "position_pct": cf(sig.get("advisory_position_pct")),
                "rationale": _clean_str(sig.get("advisory_rationale")),
                "kelly_target": cf(sig.get("kelly_target")),
                "score": cf(sig.get("score")),
            },
            "factors": {
                "value_z": cf(sig.get("value_z")),
                "quality_z": cf(sig.get("quality_z")),
                "lowvol_z": cf(sig.get("lowvol_z")),
                "size_z": cf(sig.get("size_z")),
                "multifactor_composite": cf(sig.get("multifactor_composite")),
                "xsec_12_1m": cf(sig.get("xsec_12_1m")),
                "xsec_momentum_rank": cf(sig.get("xsec_momentum_rank")),
                "score_components": _clean_components(sig.get("score_components")),
            },
            "ranges": {
                "buy_range": _clean_str(sig.get("buy_range")),
                "sell_range": _clean_str(sig.get("sell_range")),
            },
            "risk": {
                "news_sentiment": cf(sig.get("news_sentiment")),
                "covar_proxy": cf(sig.get("covar_proxy")),
                "realized_slippage": cf(sig.get("realized_slippage")),
                "mfe": cf(sig.get("mfe")),
                "mae": cf(sig.get("mae")),
                "edge_ratio": cf(sig.get("edge_ratio")),
                "hmm_risk_on": cf(sig.get("hmm_risk_on")),
                "macro_status": _clean_str(sig.get("macro_status")),
            },
            # Position-sizing decomposition — Kelly Target before vs. after the
            # HMM regime multiplier + meta-label composite were applied (ports
            # gui/panels/strategy_matrix.py::_render_regime_multiplier_impact).
            # `cf` preserves a genuine 0.0 (e.g. kelly_target_post_regime==0.0
            # when the regime multiplier zeroed sizing, or meta_label_composite
            # ==0.0 when a MetaLabeler hard-gated the signal) — never coerced
            # into a fabricated no-op. null when the active snapshot writer
            # didn't compute a value (CONSTRAINT #4).
            "sizing": {
                "kelly_target_pre_regime": cf(sig.get("kelly_target_pre_regime")),
                "kelly_target_post_regime": cf(sig.get("kelly_target_post_regime")),
                "regime_multiplier": cf(sig.get("regime_multiplier")),
                "meta_label_composite": cf(sig.get("meta_label_composite")),
                "max_position_weight": settings.MAX_POSITION_WEIGHT,
            },
            "held_by_pilots": held_by_pilots(symbol, snapshot, pilots=pilots),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("symbol_detail(%s) failed: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Symbol-vs-symbol comparison (GET /symbols/compare)
# ---------------------------------------------------------------------------

def compare_symbols(snapshot: Any, tickers: List[str]) -> Dict[str, Any]:
    """Side-by-side comparison payload for 2-5 operator-selected symbols.

    Mirrors ``gui/panels/strategy_matrix.py::_render_symbol_comparison`` — the
    same columns the legacy Streamlit table renders (final blended score,
    action, Kelly Target, conviction, GARCH vol, meta-label composite, regime
    multiplier) plus the per-module weighted score-component breakdown that
    fed its grouped bar chart.

    Every field is read straight off the SAME ``signals[]`` entry
    :func:`symbol_detail` reshapes — no recomputation, no engine import.
    ``meta_label_composite``/``regime_multiplier`` (and, on :func:`symbol_detail`,
    the fuller ``sizing`` group's ``kelly_target_pre_regime``/
    ``kelly_target_post_regime``) are now persisted by BOTH snapshot writers
    (``reporting/state_snapshot.py`` = advisory, ``main_orchestrator`` = rich —
    see ``pipeline/production_steps.py``'s sizing-decomposition threading) —
    they still honestly degrade to ``None`` when the strategy engine produced
    no value for a symbol that cycle, never a fabricated ``1.0``/``0.0``
    default (CONSTRAINT #4); this function only ever reads what is actually
    persisted, never bakes in a writer's own fallback.

    A requested symbol not found in the snapshot's ``signals[]`` (typo, or it
    rolled out of this cycle's universe) still gets a row — ``found: False``
    with an honest ``reason`` and every other leaf ``null`` — rather than
    failing the whole comparison over one bad symbol (mirrors the "one bad
    ticker can't abort a batch" convention used elsewhere in this codebase).
    Duplicate tickers (case/whitespace-insensitive) are de-duplicated,
    first-occurrence wins, preserving the caller's order.

    ``modules`` is the sorted union of every FOUND symbol's
    ``score_components`` keys — the x-axis for the grouped bar chart, computed
    here (not client-side) so every symbol renders bars on the same set of
    modules even when one symbol's aggregator skipped a module this cycle.

    Never raises (CONSTRAINT #6); degrades to ``{"as_of": None, "symbols": [],
    "modules": []}`` on totally malformed input.
    """
    try:
        cf = scoring._coerce_float
        cold_start = not isinstance(snapshot, dict)
        as_of = None if cold_start else snapshot.get("timestamp")

        rows: List[dict] = []
        seen: set = set()
        modules: set = set()
        for raw in tickers or []:
            symbol = str(raw or "").upper().strip()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)

            sig = None if cold_start else find_signal(snapshot, symbol)
            if sig is None:
                rows.append({
                    "symbol": symbol,
                    "found": False,
                    "reason": (
                        "No state snapshot yet — run the pipeline first."
                        if cold_start else
                        "Not tracked in the latest snapshot."
                    ),
                    "score": None,
                    "action": None,
                    "kelly_target": None,
                    "conviction": None,
                    "garch_vol": None,
                    "meta_label_composite": None,
                    "regime_multiplier": None,
                    "score_components": None,
                })
                continue

            components = _clean_components(sig.get("score_components"))
            if components:
                modules.update(components.keys())
            rows.append({
                "symbol": symbol,
                "found": True,
                "reason": None,
                "score": cf(sig.get("score")),
                "action": _clean_str(sig.get("advisory_action")) or _clean_str(sig.get("action")),
                "kelly_target": cf(sig.get("kelly_target")),
                "conviction": cf(sig.get("advisory_conviction")),
                "garch_vol": cf(sig.get("garch_vol")),
                "meta_label_composite": cf(sig.get("meta_label_composite")),
                "regime_multiplier": cf(sig.get("regime_multiplier")),
                "score_components": components,
            })

        return {"as_of": as_of, "symbols": rows, "modules": sorted(modules)}
    except Exception as exc:  # noqa: BLE001
        logger.debug("compare_symbols(%s) failed: %s", tickers, exc)
        return {"as_of": None, "symbols": [], "modules": []}

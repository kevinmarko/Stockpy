"""
pilots/radar_ranking.py — "Today's Radar": a small, ranked, explainable feed
=============================================================================

Answers "what is the model currently paying attention to, and why" — a
signal-based discovery feed, additive to the attribute-based Symbol Screener
(``pilots/screener`` / ``data/fmp_screener.py``), NOT a replacement for it.

Zero new scoring logic (per the introducing plan's explicit scope boundary):
this module reads and ranks the SAME already-computed, already-persisted
``Multifactor_Composite`` cross-sectional z-score
(``signals/multifactor.py::pre_compute()``, surfaced in
``output/state_snapshot.json``'s per-signal dict as ``multifactor_composite``)
that ``pilots/symbols.py::symbol_detail`` already exposes per-ticker. No new
composite/weight is invented here.

Design invariants (identical to ``pilots/symbols.py``, the closest sibling —
this module ranks the SAME ``signals[]`` list that file reads per-ticker):

* **Read-only / persisted-state only** — takes an already-loaded snapshot
  dict as an argument (mirrors ``pilots.symbols.find_signal(snapshot,
  ticker)``'s convention) rather than loading the file itself, so the caller
  controls path resolution. Imports only stdlib + ``typing`` — no
  ``settings``, no ``pilots.*``, no heavy engine — so this stays inside
  ``api/pilots_api.py``'s AST import guard with zero risk of drift.
* **Never beyond the tracked universe (CONSTRAINT: explicit scope
  boundary)** — ``state_snapshot.json``'s ``signals[]`` list can carry
  entries beyond the tracked ``tickers`` list (confirmed live: a benchmark
  proxy row, e.g. ``SPY``, is included in ``signals[]`` for macro-comparison
  purposes but is NOT part of the tracked universe). Radar restricts ranking
  strictly to symbols in the snapshot's own ``tickers`` list — never the raw
  ``signals[]`` set — so a benchmark/proxy row can never appear in the feed.
* **Honesty (CONSTRAINT #4)** — a symbol with no computed
  ``multifactor_composite`` this cycle (confirmed live: a real, non-trivial
  fraction of a cycle's universe can have this field NaN/null — missing
  fundamentals, a not-yet-covered ticker, etc.) is EXCLUDED from the feed
  entirely, never backfilled with a placeholder or a neighboring value. Each
  sub-factor clause in the templated reason string is independently
  null-checked — a composite can be present while a sub-factor is absent
  (confirmed live), so "the composite exists" must never be treated as proof
  every sub-factor does too.
* **Never raises (CONSTRAINT #6)** — degrades to an empty feed with an honest
  ``reason`` on any malformed/missing/cold-start input.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["radar_feed"]

_DEFAULT_LIMIT = 10
_MAX_LIMIT = 50

# Sub-factor keys read off each signals[] entry, in display-priority order,
# paired with the human-readable label used in the templated reason string.
_SUB_FACTORS: List[tuple] = [
    ("value_z", "Value Z"),
    ("quality_z", "Quality Z"),
    ("lowvol_z", "Low-Vol Z"),
    ("size_z", "Size Z"),
]

# How many of the (present, non-None) sub-factors get named in the reason
# string -- the largest-magnitude ones are the most explanatory.
_REASON_SUB_FACTOR_COUNT = 2


def _coerce_float(value: Any) -> Optional[float]:
    """Coerce ``value`` to a finite float, or ``None`` (never NaN/inf)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
        return None
    return f


def _clean_str(value: Any) -> Optional[str]:
    """Strip a display string; empty (or ``None``) -> ``None``."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _fmt_signed(value: float) -> str:
    """``+1.8`` / ``-0.4`` -- one decimal, always signed."""
    return f"{value:+.1f}"


def _reason_for(rank: int, sig: Dict[str, Any]) -> str:
    """Build a templated, honest, plain-English reason string for one entry.

    Every clause traces to a real, already-persisted number. The base clause
    is always grounded in ``multifactor_composite`` (the caller guarantees
    this is non-None before calling); the parenthetical sub-factor detail
    names only the ``_REASON_SUB_FACTOR_COUNT`` largest-magnitude sub-factors
    that are ACTUALLY present (non-None) for this symbol -- a missing
    sub-factor is silently omitted from the sentence, never claimed with a
    fabricated placeholder value.
    """
    if rank == 0:
        base = "Highest Multifactor Composite in the tracked universe today"
    else:
        base = f"#{rank + 1} by Multifactor Composite in the tracked universe today"

    present = [
        (label, val)
        for key, label in _SUB_FACTORS
        if (val := _coerce_float(sig.get(key))) is not None
    ]
    present.sort(key=lambda p: abs(p[1]), reverse=True)
    top = present[:_REASON_SUB_FACTOR_COUNT]
    if not top:
        return base + "."
    detail = ", ".join(f"{label} {_fmt_signed(val)}" for label, val in top)
    return f"{base} ({detail})."


def radar_feed(
    snapshot: Any,
    limit: int = _DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """Return the Top-``limit`` symbols ranked by ``Multifactor_Composite``,
    restricted to the tracked universe, each with a templated reason string.

    Returns ``{"as_of": <timestamp|None>, "items": [...], "reason":
    <str|None>}``. ``reason`` carries an honest explanation whenever
    ``items`` is empty (no snapshot yet, malformed snapshot, or a real
    snapshot with no symbol carrying a computed composite this cycle) --
    never a fabricated example/demo row. ``limit`` is clamped to
    ``[1, 50]``. Never raises (CONSTRAINT #6).
    """
    try:
        if not isinstance(snapshot, dict):
            return {
                "as_of": None,
                "items": [],
                "reason": "No state snapshot yet — run the pipeline first.",
            }

        signals = snapshot.get("signals")
        if not isinstance(signals, list):
            return {
                "as_of": snapshot.get("timestamp"),
                "items": [],
                "reason": "State snapshot malformed or missing signals.",
            }

        raw_tickers = snapshot.get("tickers")
        if not isinstance(raw_tickers, list) or not raw_tickers:
            return {
                "as_of": snapshot.get("timestamp"),
                "items": [],
                "reason": "No tracked universe in the latest snapshot.",
            }
        universe = {str(t).upper().strip() for t in raw_tickers if t}

        try:
            cap = max(1, min(int(limit), _MAX_LIMIT))
        except (TypeError, ValueError):
            cap = _DEFAULT_LIMIT

        candidates: List[tuple] = []
        for sig in signals:
            if not isinstance(sig, dict):
                continue
            symbol = _clean_str(sig.get("symbol"))
            if not symbol:
                continue
            symbol = symbol.upper()
            # Never score beyond the tracked universe (excludes e.g. a
            # benchmark/proxy row like SPY that state_snapshot.json's
            # signals[] can carry but tickers[] does not).
            if symbol not in universe:
                continue
            mc = _coerce_float(sig.get("multifactor_composite"))
            if mc is None:
                # Partial/missing coverage this cycle -- excluded, never
                # backfilled with a placeholder (CONSTRAINT #4).
                continue
            candidates.append((symbol, mc, sig))

        if not candidates:
            return {
                "as_of": snapshot.get("timestamp"),
                "items": [],
                "reason": "No signals computed yet for this cycle.",
            }

        # Composite descending, symbol ascending for a stable, deterministic
        # order on ties.
        candidates.sort(key=lambda c: (-c[1], c[0]))

        items: List[Dict[str, Any]] = []
        for rank, (symbol, mc, sig) in enumerate(candidates[:cap]):
            items.append({
                "symbol": symbol,
                "rank": rank + 1,
                "multifactor_composite": mc,
                "value_z": _coerce_float(sig.get("value_z")),
                "quality_z": _coerce_float(sig.get("quality_z")),
                "lowvol_z": _coerce_float(sig.get("lowvol_z")),
                "size_z": _coerce_float(sig.get("size_z")),
                "sector": _clean_str(sig.get("sector")),
                "price": _coerce_float(sig.get("price")),
                "reason": _reason_for(rank, sig),
            })

        return {
            "as_of": snapshot.get("timestamp"),
            "items": items,
            "reason": None,
        }
    except Exception as exc:  # noqa: BLE001 — never raises (CONSTRAINT #6)
        logger.debug("radar_feed failed: %s", exc)
        return {"as_of": None, "items": [], "reason": "Radar computation failed."}

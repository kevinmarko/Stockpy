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
  and ``scoring.pilot_holdings``; imports only ``pilots.catalog`` +
  ``pilots.scoring`` (no heavy engines), so ``api/pilots_api.py`` stays inside
  the import-guard allow-list.
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

from pilots import catalog, scoring

logger = logging.getLogger(__name__)

__all__ = ["find_signal", "held_by_pilots", "list_universe", "symbol_detail"]


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
            "held_by_pilots": held_by_pilots(symbol, snapshot, pilots=pilots),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("symbol_detail(%s) failed: %s", ticker, exc)
        return None

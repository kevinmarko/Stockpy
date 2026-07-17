"""pilots/attribution.py — portfolio-level attribution analytics for the Pilots PWA.

Two independent, honestly-degrading sections, both pure functions taking
already-fetched inputs (never doing I/O themselves):

* **Factor exposure** (:func:`portfolio_factor_exposure`) — a position-size-
  weighted average of the Value/Quality/LowVol/Size/Composite z-scores
  ``signals/multifactor.py`` computes per symbol and
  ``reporting/state_snapshot.py`` / ``main_orchestrator._write_state_snapshot``
  already persist onto ``output/state_snapshot.json``'s ``signals[]`` entries
  (``value_z``, ``quality_z``, ``lowvol_z``, ``size_z``,
  ``multifactor_composite``). Reads only a caller-supplied snapshot dict +
  ``{symbol: market_value}`` map — no file I/O, no heavy engine import.

* **Correlation clusters** (:func:`portfolio_correlation_clusters`) — thin
  shaping layer over ``research_engine.compute_correlation_clusters`` (Lopez de
  Prado distance + Ward linkage; NOT one of the heavy engines
  ``api/pilots_api.py``'s AST guard forbids — it imports only
  pandas/numpy/scipy). The caller supplies an already-built daily-returns
  DataFrame (``api/pilots_api.py`` builds this from
  ``data.historical_store.HistoricalStore.get_bars()`` — the SAME
  incrementally-cached bars source the rest of the platform reads, not a fresh
  live yfinance download via ``research_engine.fetch_returns_for_clustering``)
  so this module stays free of the pandas/scipy import until the correlation
  path is actually used (lazy import inside the function body).

Deliberately NOT on the ultra-light "stdlib + settings only" allowlist the
sibling readers (``pilots/scoring.py``, ``pilots/strategy_matrix.py``, ...)
promise — the correlation-cluster math genuinely needs vectorized
pandas/numpy/scipy, which cannot be reproduced in pure stdlib. This is a
deliberate, scoped exception: ``research_engine`` is confirmed off
``tests/test_pilots_api.py``'s heavy-engine deny-list.

Honesty rules (CONSTRAINT #4 / #6), preserved throughout:

* A held symbol absent from the pipeline snapshot, or with a non-positive/NaN
  market value, contributes NOTHING to the factor-exposure weighted average —
  it is never zero-filled. ``coverage`` reports how much of portfolio value the
  exposure numbers actually describe.
* A factor missing from a MATCHED symbol's snapshot entry (e.g. an older
  advisory-path snapshot without multifactor fields) is excluded from that
  factor's own weighted average independently — one missing factor never
  drags down or fabricates another.
* Every public function degrades to an honest empty shape + ``reason`` on
  missing/malformed input; nothing here raises.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "portfolio_factor_exposure",
    "portfolio_correlation_clusters",
]

_FACTOR_KEYS = ("value_z", "quality_z", "lowvol_z", "size_z", "multifactor_composite")


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


def _snapshot_signals_by_symbol(snapshot: Optional[dict]) -> Dict[str, dict]:
    """Index a state-snapshot dict's ``signals[]`` list by upper-cased symbol."""
    out: Dict[str, dict] = {}
    if not isinstance(snapshot, dict):
        return out
    signals = snapshot.get("signals")
    if not isinstance(signals, list):
        return out
    for sig in signals:
        if not isinstance(sig, dict):
            continue
        sym = str(sig.get("symbol") or "").upper().strip()
        if sym:
            out[sym] = sig
    return out


# ---------------------------------------------------------------------------
# Factor exposure
# ---------------------------------------------------------------------------

def portfolio_factor_exposure(
    snapshot: Optional[dict],
    held_market_values: Dict[str, float],
) -> Dict[str, Any]:
    """Position-size-weighted average factor exposure across HELD symbols.

    Parameters
    ----------
    snapshot:
        The parsed ``output/state_snapshot.json`` dict (e.g. from
        ``pilots.scoring.load_snapshot()``), or ``None``/malformed.
    held_market_values:
        ``{symbol: market_value}`` for every currently-held position (quantity
        > 0). A non-positive or ``NaN`` market value is honest for "held but no
        usable value to weight by" and is excluded from weighting (never
        coerced to a fabricated positive number).

    Returns
    -------
    dict with keys:

    * ``as_of`` — the snapshot's ``timestamp``, or ``None``.
    * ``exposures`` — ``{value_z, quality_z, lowvol_z, size_z,
      multifactor_composite}``, each the market-value-weighted average across
      matched holdings, or ``None`` when zero holdings carry that factor.
    * ``coverage`` — ``{held_count, matched_count, matched_value_pct,
      unmatched_symbols}``; ``matched_value_pct`` is the fraction of total held
      market value the exposure numbers actually describe (``None`` when total
      value is zero/unknown).
    * ``reason`` — honest explanation when there is nothing to show, else
      ``None``.
    """
    empty_exposures = {k: None for k in _FACTOR_KEYS}
    held_symbols = sorted(str(s).upper() for s in (held_market_values or {}))

    if not held_market_values:
        return {
            "as_of": None,
            "exposures": dict(empty_exposures),
            "coverage": {
                "held_count": 0,
                "matched_count": 0,
                "matched_value_pct": None,
                "unmatched_symbols": [],
            },
            "reason": "no held positions",
        }

    sig_by_symbol = _snapshot_signals_by_symbol(snapshot)
    as_of = snapshot.get("timestamp") if isinstance(snapshot, dict) else None

    total_value = 0.0
    for v in held_market_values.values():
        fv = _coerce_float(v)
        if fv is not None and fv > 0:
            total_value += fv

    weighted_sums = {k: 0.0 for k in _FACTOR_KEYS}
    weighted_weights = {k: 0.0 for k in _FACTOR_KEYS}
    matched_value = 0.0
    matched_symbols: List[str] = []
    unmatched_symbols: List[str] = []

    for sym, mv in held_market_values.items():
        sym_u = str(sym).upper().strip()
        fv = _coerce_float(mv)
        sig = sig_by_symbol.get(sym_u)
        if sig is None or fv is None or fv <= 0:
            unmatched_symbols.append(sym_u)
            continue
        matched_symbols.append(sym_u)
        matched_value += fv
        for k in _FACTOR_KEYS:
            fk = _coerce_float(sig.get(k))
            if fk is not None:
                weighted_sums[k] += fk * fv
                weighted_weights[k] += fv

    exposures = {
        k: (weighted_sums[k] / weighted_weights[k]) if weighted_weights[k] > 0 else None
        for k in _FACTOR_KEYS
    }

    reason = None
    if snapshot is None:
        reason = "no pipeline snapshot yet"
    elif not matched_symbols:
        reason = "none of your held symbols matched the latest pipeline snapshot"

    return {
        "as_of": as_of,
        "exposures": exposures,
        "coverage": {
            "held_count": len(held_symbols),
            "matched_count": len(matched_symbols),
            "matched_value_pct": (matched_value / total_value) if total_value > 0 else None,
            "unmatched_symbols": sorted(unmatched_symbols),
        },
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Correlation clusters
# ---------------------------------------------------------------------------

def portfolio_correlation_clusters(
    returns_df: Any,
    held_market_values: Dict[str, float],
    *,
    distance_threshold: float = 0.4,
    min_obs: int = 20,
) -> Dict[str, Any]:
    """Hierarchical correlation clustering of held symbols, weighted by position size.

    Parameters
    ----------
    returns_df:
        A pandas DataFrame of daily returns (columns = symbols, index = dates)
        built by the caller (``api/pilots_api.py`` sources this from
        ``HistoricalStore.get_bars()``). ``None``/empty degrades honestly.
    held_market_values:
        ``{symbol: market_value}`` for every currently-held position — used to
        compute each cluster's aggregate portfolio weight so the caller can see
        hidden concentration risk (e.g. "these 3 holdings are 40% of the book
        and move together").
    distance_threshold, min_obs:
        Passed through to ``research_engine.compute_correlation_clusters``.

    Returns
    -------
    dict with keys:

    * ``clusters`` — list of ``{cluster_id, symbols, n_symbols,
      avg_intra_corr, weight_pct, insufficient_history}``, sorted by
      ``weight_pct`` descending. ``cluster_id == 0`` is
      ``research_engine``'s "insufficient history to cluster" bucket
      (``insufficient_history=True``); it is not a real correlation grouping.
    * ``reason`` — honest explanation when ``clusters`` is empty, else
      ``None``.

    Never raises (CONSTRAINT #6); never fabricates a correlation or weight
    (CONSTRAINT #4) — a symbol whose weight can't be computed contributes
    ``0.0`` to its cluster's aggregate rather than being guessed.
    """
    if not held_market_values:
        return {"clusters": [], "reason": "no held positions"}
    if returns_df is None or getattr(returns_df, "empty", True):
        return {"clusters": [], "reason": "no return history available for held positions"}

    try:
        from research_engine import compute_correlation_clusters
    except Exception as exc:  # pragma: no cover - defensive, should not happen
        logger.warning("portfolio_correlation_clusters: research_engine unavailable: %s", exc)
        return {"clusters": [], "reason": "clustering engine unavailable"}

    try:
        labels, summary = compute_correlation_clusters(
            returns_df, distance_threshold=distance_threshold, min_obs=min_obs
        )
    except Exception as exc:  # noqa: BLE001 - dead-letter: never crash the endpoint
        logger.warning("compute_correlation_clusters failed: %s", exc)
        return {"clusters": [], "reason": "clustering failed"}

    if not labels:
        return {"clusters": [], "reason": "not enough return history to cluster"}

    total_value = 0.0
    for v in held_market_values.values():
        fv = _coerce_float(v)
        if fv is not None and fv > 0:
            total_value += fv

    summary_by_id: Dict[int, Any] = {}
    if summary is not None and not getattr(summary, "empty", True):
        for _, row in summary.iterrows():
            try:
                summary_by_id[int(row["cluster_id"])] = row
            except (TypeError, ValueError, KeyError):
                continue

    by_cluster: Dict[int, List[str]] = {}
    for sym, cid in labels.items():
        try:
            cid_i = int(cid)
        except (TypeError, ValueError):
            continue
        by_cluster.setdefault(cid_i, []).append(str(sym).upper())

    clusters: List[Dict[str, Any]] = []
    for cid, syms in by_cluster.items():
        cluster_value = 0.0
        for s in syms:
            fv = _coerce_float(held_market_values.get(s))
            if fv is not None and fv > 0:
                cluster_value += fv
        row = summary_by_id.get(cid)
        avg_corr = _coerce_float(row.get("avg_intra_corr")) if row is not None else None
        clusters.append({
            "cluster_id": cid,
            "symbols": sorted(syms),
            "n_symbols": len(syms),
            "avg_intra_corr": avg_corr,
            "weight_pct": (cluster_value / total_value) if total_value > 0 else None,
            "insufficient_history": cid == 0,
        })

    clusters.sort(key=lambda c: (-(c["weight_pct"] or 0.0), c["cluster_id"]))

    return {"clusters": clusters, "reason": None}

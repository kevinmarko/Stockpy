"""
sector_selection_engine.py — Semantic Related Sector Selection: Daily
Engine
==============================================================================
Orchestrates the full ranking pipeline for one or more target symbols:

    sector membership -> sector + target descriptions -> embeddings ->
    cosine similarity -> Sector Heat Factor -> correlation_coefficient ->
    rank -> top-N selection -> persist (data/sector_correlation_store.py)

Flat, top-level module — this repo's "Engine" architecture convention (no
package directory, imported directly by orchestrators).

Every row's ``correlation_coefficient`` is ``None`` (never a fabricated
value — CONSTRAINT #4) whenever either its similarity or heat input is
unavailable; ``degraded_reason`` records why (see ``_rank_one_target``).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def run_sector_selection(
    targets: List[str],
    *,
    as_of: Optional[datetime] = None,
    top_n: Optional[int] = None,
    historical_store: Optional[Any] = None,
    correlation_store: Optional[Any] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Compute (and best-effort persist) the full candidate-sector ranking
    for each symbol in ``targets``.

    Returns ``{target_symbol: [row, ...]}`` — the same rows persisted,
    ranked ascending by ``rank`` (unranked rows last, ``rank=None``). One
    target's failure never blocks the others (per-target try/except,
    matching the ticker-loop convention in ``data_engine.py``/
    orchestrators — CONSTRAINT #6). A persistence failure is logged and
    swallowed; it never discards the computed ranking from the return
    value.

    Returns ``{}`` immediately, with no DB/network activity, when
    ``settings.SECTOR_SELECTION_ENABLED`` is False or ``targets`` is empty.
    """
    from settings import settings

    if not settings.SECTOR_SELECTION_ENABLED or not targets:
        return {}

    from data.sector_selection_heat import compute_spec_sector_heat
    from data.sector_embeddings import load_sector_descriptions
    from engine.portfolio_exposure import _load_sector_map

    if historical_store is None:
        from data.historical_store import HistoricalStore
        historical_store = HistoricalStore()
    if correlation_store is None:
        correlation_store = _build_correlation_store()

    ticker_sector_map = _load_sector_map()
    sector_descriptions = load_sector_descriptions()
    sectors = sorted(sector_descriptions.keys())
    n = int(top_n) if top_n is not None else int(settings.SECTOR_SELECTION_TOP_N)
    embedder = (settings.SECTOR_SIMILARITY_EMBEDDER or "none").lower()
    pooling = settings.SECTOR_SIMILARITY_POOLING

    # Resolve "now" exactly ONCE and thread it through every as-of-sensitive
    # call below -- letting compute_spec_sector_heat independently default
    # to its own datetime.now() would let the persisted as_of and the
    # heat-window's as_of drift apart across a wall-clock tick between the
    # two calls (a subtle, if rare, self-inconsistency).
    resolved_now = as_of or datetime.now(timezone.utc)
    resolved_as_of = historical_store.resolve_trading_day(resolved_now)

    heat_by_sector = compute_spec_sector_heat(
        sectors, ticker_sector_map=ticker_sector_map, as_of=resolved_now, historical_store=historical_store,
    )

    results: Dict[str, List[Dict[str, Any]]] = {}
    for target in targets:
        try:
            rows = _rank_one_target(
                target, sectors, sector_descriptions, heat_by_sector,
                embedder=embedder, pooling=pooling,
                historical_store=historical_store, top_n=n,
                as_of=resolved_now,
            )
        except Exception as exc:
            logger.warning("run_sector_selection: target %r failed: %s", target, exc)
            rows = []
        results[target] = rows

        if rows and correlation_store is not None:
            try:
                correlation_store.record_correlations(
                    rows, as_of=resolved_as_of, target_symbol=target,
                )
            except Exception as exc:
                logger.warning("run_sector_selection: persist failed for %s: %s", target, exc)

    return results


def _rank_one_target(
    target: str,
    sectors: List[str],
    sector_descriptions: Dict[str, str],
    heat_by_sector: Dict[str, Dict[str, Any]],
    *,
    embedder: str,
    pooling: str,
    historical_store: Any,
    top_n: int,
    as_of: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    from data.sector_embeddings import SBERT_AVAILABLE, cosine_similarity, resolve_target_description

    target_description = resolve_target_description(
        target, historical_store=historical_store, as_of=as_of,
    )
    embedder_ready = embedder == "openai" or (embedder == "sbert" and SBERT_AVAILABLE)
    target_vector = _embed(target_description, embedder=embedder, pooling=pooling)

    rows: List[Dict[str, Any]] = []
    for sector in sectors:
        heat = heat_by_sector.get(sector, {})
        shf = heat.get("shf", float("nan"))
        news_volume = heat.get("news_volume", float("nan"))
        review_volume = heat.get("review_volume", float("nan"))
        heat_degraded_reason = heat.get("degraded_reason")

        sector_description = sector_descriptions.get(sector)
        sector_vector = _embed(sector_description, embedder=embedder, pooling=pooling)
        cos = cosine_similarity(target_vector, sector_vector)

        similarity_reason = None
        if math.isnan(cos):
            if embedder == "none" or not embedder_ready:
                similarity_reason = "no_embedder"
            elif target_description is None:
                similarity_reason = "no_target_description"
            elif not sector_description:
                similarity_reason = "no_sector_description"
            else:
                similarity_reason = "embedding_failed"

        # `similarity_reason` wins whenever it's set (secondary audit,
        # 2026-08-24 -- was `heat_degraded_reason or similarity_reason`,
        # backwards): `heat_degraded_reason` (e.g. "review_unavailable") is a
        # deliberately broad provenance flag stamped even when `shf`
        # computed fine and `coefficient` is a genuinely valid number (see
        # tests/test_sector_selection_engine.py -- that priority is
        # intentional and preserved here for the cos-is-valid case). But
        # when `cos` is itself NaN, `similarity_reason` IS the actual reason
        # `coefficient` is None -- the old precedence let a routine heat
        # flag silently mask that real, blocking cause (e.g. reporting
        # "review_unavailable" for a row that was actually
        # "no_target_description" and could never have scored regardless of
        # heat). `similarity_reason` is None whenever `cos` is valid, so this
        # still falls through to `heat_degraded_reason` in that case.
        degraded_reason = similarity_reason or heat_degraded_reason
        coefficient = float("nan") if (math.isnan(cos) or math.isnan(shf)) else cos * shf
        ingestion_volume = _nan_aware_sum(news_volume, review_volume)

        rows.append({
            "sector": sector,
            "cosine_similarity": _none_if_nan(cos),
            "ingestion_volume": _none_if_nan(ingestion_volume),
            "sector_heat_factor": _none_if_nan(shf),
            "correlation_coefficient": _none_if_nan(coefficient),
            "degraded_reason": degraded_reason,
            "embedder": embedder,
            "pooling": pooling if embedder == "sbert" else None,
        })

    ranked = sorted(
        rows,
        key=lambda r: (r["correlation_coefficient"] is None, -(r["correlation_coefficient"] or 0.0)),
    )
    for i, row in enumerate(ranked):
        if row["correlation_coefficient"] is not None:
            row["rank"] = i + 1
            row["selected"] = row["rank"] <= top_n
        else:
            row["rank"] = None
            row["selected"] = False
    return ranked


def _embed(text: Optional[str], *, embedder: str, pooling: str):
    if not text:
        return None
    if embedder == "sbert":
        from data.sector_embeddings import embed_text
        return embed_text(text, pooling=pooling)
    if embedder == "openai":
        return _embed_via_openai(text)
    return None


def _embed_via_openai(text: str):
    try:
        from llm.router import get_sector_embedding_provider
        provider = get_sector_embedding_provider()
        if provider is None:
            return None
        vectors = provider.embed_texts([text])
        if not vectors:
            return None
        return vectors[0]
    except Exception as exc:
        logger.debug("_embed_via_openai failed: %s", exc)
        return None


def _nan_aware_sum(*values: float) -> float:
    """Sum, treating NaN operands as absent rather than poisoning the
    result -- ``ingestion_volume`` should reflect whatever volume WAS
    observed (e.g. news-only when review is degraded), not become NaN
    just because one term is unavailable. All-NaN inputs -> NaN."""
    real = [v for v in values if not math.isnan(v)]
    return float("nan") if not real else float(sum(real))


def _none_if_nan(value: float) -> Optional[float]:
    return None if math.isnan(value) else float(value)


def _build_correlation_store():
    """Lazily construct a real ``SectorCorrelationStore``, degrading to the
    offline stand-in on connectivity failure — mirrors
    ``StrategyEngine._get_cap_audit_store``'s pattern (CONSTRAINT #6: a DB
    outage never blocks the engine's own ranking computation, only the
    durable persistence of it)."""
    from data.sector_correlation_store import SectorCorrelationStore, _OfflineSectorCorrelationStore
    try:
        return SectorCorrelationStore()
    except Exception as exc:
        logger.warning(
            "SectorCorrelationStore unavailable (%s: %s); correlations will "
            "compute but not persist.", type(exc).__name__, exc,
        )
        return _OfflineSectorCorrelationStore()

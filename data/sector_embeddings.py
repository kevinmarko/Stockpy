"""
data/sector_embeddings.py — Semantic Related Sector Selection: Embeddings
============================================================================
Loads sector/target descriptions, computes SBERT (or OpenAI, via
``llm.router.get_sector_embedding_provider``) embeddings, and computes
cosine similarity between a target stock and each candidate sector — the
similarity term of ``correlation_coefficient = cosine_similarity * SHF``
(see ``data/sector_selection_heat.py`` for the SHF term).

``SBERT_AVAILABLE`` guard mirrors ``forecasting_engine.py``'s
``TENSORFLOW_AVAILABLE`` pattern: absent the optional
``sentence-transformers`` package (``requirements-optional.txt``), every
embedding call degrades to ``None`` and every ``cosine_similarity`` result
is ``NaN`` — never a fabricated value (CONSTRAINT #4).

Pooling caveat (documented, not hidden)
-----------------------------------------
The source methodology specifies MAX-pooling. The default model,
``sentence-transformers/all-MiniLM-L6-v2``, ships configured for MEAN
pooling and was trained that way — max-pooled output from this checkpoint
is off-distribution. ``settings.SECTOR_SIMILARITY_POOLING`` defaults to
``"max"`` (spec-faithful) rather than silently substituting ``"mean"``, so
the difference is measurable, not assumed.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import yaml

from settings import settings

logger = logging.getLogger(__name__)

try:
    from sentence_transformers import SentenceTransformer, models  # noqa: F401
    SBERT_AVAILABLE = True
except ImportError:
    SBERT_AVAILABLE = False

if not SBERT_AVAILABLE:
    logger.debug("sentence-transformers not available. Sector similarity will degrade to NaN.")

_DESCRIPTIONS_PATH = Path(__file__).resolve().parent / "sector_descriptions.yaml"
# Resolved from settings.OUTPUT_DIR (settings.LOCAL_DATA_ROOT / "output" by
# default) rather than a CWD-relative Path("output") literal.
_EMBEDDING_CACHE_PATH = settings.OUTPUT_DIR / "sector_embedding_cache.json"

_MODEL_CACHE: Dict[tuple, Any] = {}


def load_sector_descriptions(path: Optional[Path] = None) -> Dict[str, str]:
    """Return ``{sector_name: description}`` from the committed YAML.

    ``{}`` on any read/parse failure (CONSTRAINT #6) — a caller cannot
    compute similarity for a sector whose description failed to load, so
    it degrades the same way an unrecognized sector does.
    """
    try:
        with open(path or _DESCRIPTIONS_PATH, "r") as f:
            data = yaml.safe_load(f) or {}
        return {str(k): str(v) for k, v in (data.get("sectors") or {}).items()}
    except Exception as exc:
        logger.warning("load_sector_descriptions failed: %s", exc)
        return {}


def resolve_target_description(
    symbol: str,
    *,
    historical_store: Optional[Any] = None,
    descriptions_path: Optional[Path] = None,
    as_of: Optional[Any] = None,
) -> Optional[str]:
    """Resolve a target stock's business description for embedding.

    Resolution order, all honest (CONSTRAINT #4 — never synthesized from
    ticker + sector name, which would produce a similarity number with no
    real information behind it):

    1. An operator-authored override in ``sector_descriptions.yaml``'s
       ``targets:`` block.
    2. ``fundamentals_history.raw_json['longBusinessSummary']`` (read-only —
       this never triggers a live fetch, so this never makes a network
       call). When ``as_of`` is given, resolved point-in-time via
       ``HistoricalStore.get_fundamentals_raw_json_asof`` (the most recent
       row whose ``report_date <= as_of`` — never a later, future-relative
       description); when ``as_of`` is ``None`` (every existing caller today
       — the live daily pipeline has no "as of a past date" concept), the
       most recently cached row is used exactly as before this parameter
       existed.
    3. ``None`` — the caller must treat this as "similarity unavailable
       for this target", never fall back to a fabricated description.

    ``as_of`` closes a confirmed lookahead-bias gap (secondary audit,
    2026-08-24): this function previously had NO point-in-time awareness at
    all, so a future backtest/replay caller scoring a past date would
    silently embed the company's CURRENT business description regardless of
    what date was being scored — see
    docs/known_issues/sector_selection_similarity_lookahead.md. Dormant
    until a caller passes ``as_of`` (today, only ``sector_selection_engine``
    threads it, and does so via ``resolved_now`` -- effectively a no-op for
    the one real production caller, which always scores "now").
    """
    symbol_upper = str(symbol).upper()
    try:
        with open(descriptions_path or _DESCRIPTIONS_PATH, "r") as f:
            data = yaml.safe_load(f) or {}
        override = (data.get("targets") or {}).get(symbol_upper)
        if override:
            return str(override)
    except Exception as exc:
        logger.debug("resolve_target_description: override lookup failed: %s", exc)

    try:
        if historical_store is None:
            from data.historical_store import HistoricalStore
            historical_store = HistoricalStore()

        if as_of is not None:
            raw_json_str = historical_store.get_fundamentals_raw_json_asof(symbol_upper, as_of)
        else:
            history_df = historical_store.get_fundamentals_history(symbol_upper)
            raw_json_str = (
                history_df.iloc[-1].get("raw_json")
                if history_df is not None and not history_df.empty
                else None
            )
        if not raw_json_str:
            return None
        parsed = json.loads(raw_json_str)
        if not isinstance(parsed, dict):
            return None
        summary = parsed.get("longBusinessSummary")
        return str(summary) if summary else None
    except Exception as exc:
        logger.debug("resolve_target_description: fundamentals lookup failed for %s: %s", symbol_upper, exc)
        return None


def embed_text(
    text: Optional[str],
    *,
    model_name: Optional[str] = None,
    pooling: Optional[str] = None,
) -> Optional[np.ndarray]:
    """Embed ``text`` with the configured SBERT model. ``None`` when
    ``sentence-transformers`` is unavailable, ``text`` is empty, or
    embedding fails for any reason (CONSTRAINT #6 — never raises)."""
    if not SBERT_AVAILABLE or not text:
        return None
    from settings import settings

    model_name = model_name or settings.SECTOR_SIMILARITY_MODEL
    pooling = pooling or settings.SECTOR_SIMILARITY_POOLING

    cache_key = _content_hash(model_name, pooling, text)
    cached = _read_embedding_cache(cache_key)
    if cached is not None:
        return np.asarray(cached, dtype=float)

    try:
        model = _get_sbert_model(model_name, pooling)
        vector = np.asarray(model.encode(text, show_progress_bar=False), dtype=float)
    except Exception as exc:
        logger.warning("embed_text failed for model=%s pooling=%s: %s", model_name, pooling, exc)
        return None

    _write_embedding_cache(cache_key, vector.tolist())
    return vector


def cosine_similarity(v1: Optional[np.ndarray], v2: Optional[np.ndarray]) -> float:
    """Cosine similarity between two vectors. ``NaN`` (never a fabricated
    number) when either vector is ``None`` or has zero norm."""
    if v1 is None or v2 is None:
        return float("nan")
    a = np.asarray(v1, dtype=float)
    b = np.asarray(v2, dtype=float)
    norm_a, norm_b = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if norm_a == 0.0 or norm_b == 0.0:
        return float("nan")
    return float(np.dot(a, b) / (norm_a * norm_b))


def _get_sbert_model(model_name: str, pooling: str) -> Any:
    cache_key = (model_name, pooling)
    if cache_key in _MODEL_CACHE:
        return _MODEL_CACHE[cache_key]

    if pooling == "max":
        word_embedding_model = models.Transformer(model_name)
        pooling_model = models.Pooling(
            word_embedding_model.get_word_embedding_dimension(),
            pooling_mode_max_tokens=True,
            pooling_mode_mean_tokens=False,
            pooling_mode_cls_token=False,
        )
        model = SentenceTransformer(modules=[word_embedding_model, pooling_model])
    else:
        model = SentenceTransformer(model_name)  # ships with its own configured pooling

    _MODEL_CACHE[cache_key] = model
    return model


def _content_hash(model_name: str, pooling: str, text: str) -> str:
    return hashlib.sha256(f"{model_name}|{pooling}|{text}".encode("utf-8")).hexdigest()


def _read_embedding_cache(cache_key: str) -> Optional[list]:
    try:
        if not _EMBEDDING_CACHE_PATH.exists():
            return None
        with open(_EMBEDDING_CACHE_PATH, "r") as f:
            cache = json.load(f)
        return cache.get(cache_key)
    except Exception as exc:
        logger.debug("_read_embedding_cache failed: %s", exc)
        return None


def _write_embedding_cache(cache_key: str, vector: list) -> None:
    try:
        _EMBEDDING_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        cache: Dict[str, list] = {}
        if _EMBEDDING_CACHE_PATH.exists():
            with open(_EMBEDDING_CACHE_PATH, "r") as f:
                cache = json.load(f)
        cache[cache_key] = vector
        with open(_EMBEDDING_CACHE_PATH, "w") as f:
            json.dump(cache, f)
    except Exception as exc:
        logger.debug("_write_embedding_cache failed (non-fatal): %s", exc)

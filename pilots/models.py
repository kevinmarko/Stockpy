"""pilots/models.py — ML model registry reader for the PWA.
==========================================================

Surfaces ``ml/registry.yaml`` (the production model registry: role, trained
date, CPCV-DSR, PBO, deployable flag) for the mobile "About the models"
sub-page (``GET /models``) — a transparency/trust surface showing the honest,
gated state of the models behind the platform.

Design invariants (identical to the rest of the Pilots read layer):

* **Pure static read** — ``yaml.safe_load`` of the repo-root ``ml/registry.yaml``.
  No heavy engine, no DB, no network. Mirrors
  ``gui/panels/analytics.py::_parse_registry_rows``.
* **Honesty (CONSTRAINT #4)** — ``null`` metrics (``cpcv_dsr``/``pbo`` for an
  un-validated model) are preserved as ``None``; the UI renders "—", never a
  fabricated ``0``.
* **Never raises (CONSTRAINT #6)** — a missing/unreadable/malformed file (or a
  missing PyYAML) degrades to ``[]``.

**Webapp porting backlog rider 13b (Needs Retrain age flag):** ``needs_retrain``
and ``age_days`` are computed HERE (not left as raw date math for the
frontend) because this module already has the per-model ``trained_date`` in
hand. ``MODEL_RETRAIN_WINDOW_DAYS`` is imported live from
``gui.help_content`` — the SAME 30-day constant
``ml.meta_labeling.MetaLabeler.needs_retrain()`` uses and the existing
"Needs Retrain"/"Model Freshness" glossary entries already cite — never
re-typed as a literal here (mirrors this file's own "thresholds are live-
imported, never hard-coded" convention and ``gui/help_content.py``'s own
"Never hard-code numeric thresholds here" rule). ``api/pilots_api.py``'s
``GET /thresholds`` ALSO surfaces this same constant as
``retrain_window_days`` so the frontend's static explainer text can quote the
window without a hard-coded literal either, mirroring how ``Models.tsx``
already treats every other gate number.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["model_registry_rows"]

# pilots/ sits at the repo root, so parent.parent is the repo root.
_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "ml" / "registry.yaml"


def _parse_trained_date(value: Any) -> Optional[date]:
    """Best-effort parse of a registry ``trained_date`` value into a
    ``date``. YAML may already load it as a ``datetime.date``/``datetime``;
    a plain ISO string (``'2026-07-06'``) is the other documented shape.
    Returns ``None`` on anything else (CONSTRAINT #4: an unparseable date
    yields a null age/flag, never a fabricated one) — never raises."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_registry_rows(text: str) -> List[Dict[str, Any]]:
    """Parse ``ml/registry.yaml`` text into a flat list of model row dicts (pure).

    ``[]`` on ANY failure (PyYAML missing, malformed YAML, unexpected shape).
    ``null`` metrics preserved as ``None`` (CONSTRAINT #4). Mirrors
    ``gui/panels/analytics.py::_parse_registry_rows`` but keyed ``name`` (the
    PWA row contract) rather than ``model``.
    """
    try:
        import yaml  # PyYAML — already a repo dependency.
    except Exception as exc:  # noqa: BLE001
        logger.debug("PyYAML unavailable for registry load: %s", exc)
        return []
    try:
        raw = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("registry YAML parse failed: %s", exc)
        return []
    if not isinstance(raw, dict):
        return []
    models = raw.get("models")
    if not isinstance(models, dict):
        return []

    # Lazy import (mirrors this codebase's HistoricalStore/etc. convention) —
    # avoids paying gui.help_content's own import chain (engine.advisory,
    # validation.thresholds, gui.robinhood_execution_panel) at pilots/models.py
    # module-import time, which would otherwise run on every api/pilots_api.py
    # process start regardless of whether GET /models is ever hit.
    try:
        from gui.help_content import MODEL_RETRAIN_WINDOW_DAYS
    except Exception as exc:  # noqa: BLE001 — dead-letter (CONSTRAINT #6)
        logger.debug("MODEL_RETRAIN_WINDOW_DAYS unavailable: %s", exc)
        MODEL_RETRAIN_WINDOW_DAYS = None  # type: ignore[assignment]

    today = date.today()
    rows: List[Dict[str, Any]] = []
    for name, meta in models.items():
        if not isinstance(meta, dict):
            continue  # skip malformed entry rather than fabricating fields

        trained = _parse_trained_date(meta.get("trained_date"))
        age_days: Optional[int] = None
        needs_retrain: Optional[bool] = None
        if trained is not None and MODEL_RETRAIN_WINDOW_DAYS is not None:
            age_days = (today - trained).days
            needs_retrain = age_days >= MODEL_RETRAIN_WINDOW_DAYS

        rows.append(
            {
                "name": str(name),
                "role": meta.get("role"),
                "trained_date": _as_str_or_none(meta.get("trained_date")),
                "cpcv_dsr": meta.get("cpcv_dsr"),
                "pbo": meta.get("pbo"),
                "n_train": meta.get("n_train"),
                "deployable": meta.get("deployable"),
                "notes": meta.get("notes"),
                "age_days": age_days,
                "needs_retrain": needs_retrain,
            }
        )
    return rows


def _as_str_or_none(value: Any):
    """YAML may load a date as a ``datetime.date``; stringify for JSON honesty."""
    if value is None:
        return None
    return str(value)


def model_registry_rows() -> List[Dict[str, Any]]:
    """Resolve + parse ``ml/registry.yaml`` into row dicts, or ``[]``.

    Never raises (CONSTRAINT #6): a missing/unreadable/malformed file yields an
    empty list so the API returns an honest empty registry rather than a 500.
    """
    try:
        if not _REGISTRY_PATH.exists():
            return []
        text = _REGISTRY_PATH.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("registry file read failed: %s", exc)
        return []
    return _parse_registry_rows(text)

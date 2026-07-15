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
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

__all__ = ["model_registry_rows"]

# pilots/ sits at the repo root, so parent.parent is the repo root.
_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "ml" / "registry.yaml"


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
    rows: List[Dict[str, Any]] = []
    for name, meta in models.items():
        if not isinstance(meta, dict):
            continue  # skip malformed entry rather than fabricating fields
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

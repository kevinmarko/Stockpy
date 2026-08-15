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
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = ["model_registry_rows"]


def _scan_local_artifacts() -> Dict[str, Tuple[date, str]]:
    """Scan ``settings.LOCAL_DATA_ROOT / 'ml_models'`` in one single pass for latest .pkl artifacts.

    Returns a mapping ``{model_key: (trained_date, filename)}``. Never raises (CONSTRAINT #6).
    """
    results: Dict[str, Tuple[date, str]] = {}
    try:
        from settings import settings  # noqa: PLC0415
        models_dir = settings.LOCAL_DATA_ROOT / "ml_models"
        if not models_dir.exists():
            return results

        for p in models_dir.glob("*.pkl"):
            stem = p.stem
            # Expected patterns: lgbm_YYYYMMDD or meta_<signal>_YYYYMMDD
            parts = stem.rsplit("_", 1)
            if len(parts) != 2:
                continue
            prefix, date_part = parts[0], parts[1]
            if len(date_part) != 8 or not date_part.isdigit():
                continue

            try:
                artifact_date = datetime.strptime(date_part, "%Y%m%d").date()
            except ValueError:
                continue

            model_key: Optional[str] = None
            if prefix == "lgbm":
                model_key = "lgbm_ranker"
            elif prefix.startswith("meta_"):
                sig = prefix[5:]
                model_key = f"meta_labeler_{sig}"

            if model_key is not None:
                if model_key not in results or artifact_date > results[model_key][0]:
                    results[model_key] = (artifact_date, p.name)
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("Local artifact scan failed: %s", exc)
    return results


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


def _parse_registry_dict(raw: dict) -> List[Dict[str, Any]]:
    """Parse a registry mapping into a flat list of model row dicts (pure).

    ``[]`` on ANY failure. ``null`` metrics preserved as ``None`` (CONSTRAINT #4).
    """
    if not isinstance(raw, dict):
        return []
    models = raw.get("models")
    if not isinstance(models, dict):
        return []

    # Lazy import (mirrors this codebase's HistoricalStore/etc. convention)
    try:
        from gui.help_content import MODEL_RETRAIN_WINDOW_DAYS
    except Exception as exc:  # noqa: BLE001 — dead-letter (CONSTRAINT #6)
        logger.debug("MODEL_RETRAIN_WINDOW_DAYS unavailable: %s", exc)
        MODEL_RETRAIN_WINDOW_DAYS = None  # type: ignore[assignment]

    # Single-pass artifact scan across all models
    artifacts = _scan_local_artifacts()
    today = date.today()
    rows: List[Dict[str, Any]] = []

    for name, meta in models.items():
        if not isinstance(meta, dict):
            continue  # skip malformed entry rather than fabricating fields

        trained = _parse_trained_date(meta.get("trained_date"))
        cpcv_dsr = meta.get("cpcv_dsr")
        pbo = meta.get("pbo")
        cpcv_mean_oos_sharpe = meta.get("cpcv_mean_oos_sharpe")
        cpcv_mean_oos_max_dd = meta.get("cpcv_mean_oos_max_dd")
        n_train = meta.get("n_train")
        deployable = meta.get("deployable")

        # Self-healing discovery: if a newer dated binary artifact exists on disk, surface it
        if str(name) in artifacts:
            disc_date, disc_filename = artifacts[str(name)]
            if trained is None or disc_date > trained:
                trained = disc_date
                # If artifact on disk differs from registry's validated artifact_file,
                # do not pair unvalidated new binary with stale validation metrics (CONSTRAINT #4)
                if meta.get("artifact_file") != disc_filename:
                    cpcv_dsr = None
                    pbo = None
                    cpcv_mean_oos_sharpe = None
                    cpcv_mean_oos_max_dd = None
                    deployable = False

        age_days: Optional[int] = None
        needs_retrain: Optional[bool] = None
        if trained is not None and MODEL_RETRAIN_WINDOW_DAYS is not None:
            age_days = (today - trained).days
            needs_retrain = age_days >= MODEL_RETRAIN_WINDOW_DAYS

        rows.append(
            {
                "name": str(name),
                "role": meta.get("role"),
                "trained_date": _as_str_or_none(trained) if trained is not None else _as_str_or_none(meta.get("trained_date")),
                "cpcv_dsr": cpcv_dsr,
                "pbo": pbo,
                "cpcv_mean_oos_sharpe": cpcv_mean_oos_sharpe,
                "cpcv_mean_oos_max_dd": cpcv_mean_oos_max_dd,
                "n_train": n_train,
                "deployable": deployable,
                "notes": meta.get("notes"),
                "age_days": age_days,
                "needs_retrain": needs_retrain,
            }
        )
    return rows


def _parse_registry_rows(text: str) -> List[Dict[str, Any]]:
    """Parse ``ml/registry.yaml`` text into a flat list of model row dicts (pure)."""
    try:
        import yaml  # PyYAML — already a repo dependency.
        raw = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("registry YAML parse failed: %s", exc)
        return []
    return _parse_registry_dict(raw)


def _as_str_or_none(value: Any):
    """YAML may load a date as a ``datetime.date``; stringify for JSON honesty."""
    if value is None:
        return None
    return str(value)


def model_registry_rows() -> List[Dict[str, Any]]:
    """Resolve + parse the model registry into row dicts, or ``[]``.

    Uses ``ml.registry_io.load_registry()`` (which handles smart-merge between
    git and LOCAL_DATA_ROOT) and performs single-pass self-healing artifact discovery.
    Never raises (CONSTRAINT #6).
    """
    try:
        from ml.registry_io import load_registry  # noqa: PLC0415
        data = load_registry()
        if not data:
            return []
        return _parse_registry_dict(data)
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("registry read failed: %s", exc)
        return []

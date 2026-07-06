"""
InvestYo Quant Platform - ML Registry I/O Helper
================================================
Small, focused helper to update ``ml/registry.yaml`` in place after a training
run — load the YAML, set the metrics for a given model role, and dump it back
to disk.

Design rules
------------
- NEVER hand-splice YAML text.  We round-trip via PyYAML (the repo's declared
  dependency).  Comments are lost by PyYAML's safe dumper; we mitigate this by
  preserving key ORDER and re-emitting the leading banner comment block verbatim
  so the file stays human-readable and self-documenting.
- The deployability gate is the single source of truth:
      deployable = (cpcv_dsr is not None and cpcv_dsr > 0.95
                    and pbo is not None and pbo < 0.5)
  This mirrors the platform-wide PBO < 0.5 AND DSR > 0.95 rule in CLAUDE.md.
- Honest metrics only: if a metric could not be computed, pass ``None`` and the
  gate resolves to ``deployable = False`` (never fabricated).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("ML.RegistryIO")

_DEFAULT_REGISTRY_PATH = Path(__file__).parent / "registry.yaml"

# The leading comment banner is not preserved by PyYAML's dumper, so we re-emit
# it verbatim on write to keep the file self-documenting.
_REGISTRY_HEADER = """\
# InvestYo ML Model Registry
# ===========================
# Lists production models with their paths, training metadata, and CPCV validation metrics.
# Updated automatically by the monthly retraining job in main_orchestrator.py.
#
# Fields
# ------
# role:            what the model does in the pipeline
# path:            conventional model location, informational only — the runtime loader
#                  (<Class>.load_latest()) never reads this field; it discovers the model
#                  by glob-sorting dated ml/models/<prefix>_<YYYYMMDD>.pkl files. See
#                  artifact_file below for the exact binary behind a given run's metrics.
# trained_date:    ISO date of most recent training run
# cpcv_dsr:        Deflated Sharpe Ratio from CPCV path evaluation (target > 0.95)
# pbo:             Probability of Backtest Overfitting from CPCV (target < 0.50)
# n_train:         number of training samples in the most recent run
# notes:           any caveats, data limitations, or scope restrictions
# deployable:      true iff cpcv_dsr > 0.95 AND pbo < 0.50 AND meets Gravity gates
#
# Provenance fields (optional; captured for reproducibility — never affect `deployable`)
# ----------------------------------------------------------------------------------------
# artifact_file:   exact dated pickle filename actually written this run (e.g. lgbm_20260706.pkl)
# hyperparameters: the model's training hyperparameters (dict)
# train_window:    the data-split window {start, end, n_dates} (dates as YYYY-MM-DD strings)
# features:        the ordered feature-column list the model trained with (list)
"""


def compute_deployable(cpcv_dsr: Optional[float], pbo: Optional[float]) -> bool:
    """The single deployability gate: DSR > 0.95 AND PBO < 0.5.

    Returns ``False`` whenever either metric is ``None`` (honest — an
    uncomputable metric can never clear the gate).
    """
    return (
        cpcv_dsr is not None
        and cpcv_dsr > 0.95
        and pbo is not None
        and pbo < 0.5
    )


def load_registry(path: Optional[Path] = None) -> dict:
    """Load the registry YAML into a plain dict (empty dict on missing file)."""
    path = Path(path) if path is not None else _DEFAULT_REGISTRY_PATH
    if not path.exists():
        logger.warning("Registry file not found at %s — starting empty.", path)
        return {}
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    return data


def update_model_metrics(
    model_key: str,
    *,
    trained_date: Optional[str] = None,
    cpcv_dsr: Optional[float] = None,
    pbo: Optional[float] = None,
    n_train: Optional[int] = None,
    path: Optional[Path] = None,
    artifact_file: Optional[str] = None,
    hyperparameters: Optional[dict] = None,
    train_window: Optional[dict] = None,
    features: Optional[list] = None,
) -> dict:
    """Update ``models.<model_key>.{trained_date,cpcv_dsr,pbo,n_train,deployable}``.

    The ``deployable`` flag is (re)derived from ``cpcv_dsr``/``pbo`` via
    :func:`compute_deployable` — callers do NOT pass it directly, so the gate
    can never be spoofed.

    Provenance (all optional, backward-compatible, and independent of the gate):
    ``artifact_file`` (the exact dated pickle filename written this run),
    ``hyperparameters`` (the model's training params dict), ``train_window``
    (the data-split window ``{start, end, n_dates}``), and ``features`` (the
    ordered feature-column list). Each is written into the entry verbatim; a
    ``None`` value is stored as-is and never influences ``deployable``.

    Returns the resulting model sub-dict.  Raises ``KeyError`` if the model key
    does not already exist in the registry (we update in place, never invent
    new roles).
    """
    reg_path = Path(path) if path is not None else _DEFAULT_REGISTRY_PATH
    data = load_registry(reg_path)

    models = data.setdefault("models", {})
    if model_key not in models:
        raise KeyError(
            f"Model key '{model_key}' not found in registry {reg_path}. "
            f"Known keys: {sorted(models.keys())}"
        )

    entry = models[model_key]
    entry["trained_date"] = trained_date
    entry["cpcv_dsr"] = float(cpcv_dsr) if cpcv_dsr is not None else None
    entry["pbo"] = float(pbo) if pbo is not None else None
    entry["n_train"] = int(n_train) if n_train is not None else None
    entry["deployable"] = compute_deployable(cpcv_dsr, pbo)

    # Optional provenance — captured for reproducibility, never gate-affecting.
    entry["artifact_file"] = artifact_file
    entry["hyperparameters"] = dict(hyperparameters) if hyperparameters is not None else None
    entry["train_window"] = dict(train_window) if train_window is not None else None
    entry["features"] = list(features) if features is not None else None

    _dump_registry(data, reg_path)
    logger.info(
        "Registry updated: %s trained_date=%s dsr=%s pbo=%s n_train=%s deployable=%s",
        model_key, trained_date, cpcv_dsr, pbo, n_train, entry["deployable"],
    )
    return entry


def _dump_registry(data: dict, path: Path) -> None:
    """Write the registry back to disk, re-emitting the banner comment block."""
    body = yaml.safe_dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(_REGISTRY_HEADER)
        f.write("\n")
        f.write(body)

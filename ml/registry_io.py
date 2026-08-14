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
# Updated automatically by the monthly retraining job scripts/retrain_models.py
# (scheduled via the launchd plist scripts/com.investyo.monthly-retrain.plist).
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
# cpcv_mean_oos_sharpe:  mean out-of-sample Sharpe across CPCV held-out paths for the
#                  SAME DSR-selected strategy that produced cpcv_dsr/pbo (never read by the
#                  deployable gate)
# cpcv_mean_oos_max_dd:  mean out-of-sample max drawdown across CPCV held-out paths for the
#                  SAME DSR-selected strategy (never read by the deployable gate)
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


def resolve_registry_path(path: Optional[Path] = None) -> Path:
    """Resolve the registry YAML path.

    Priority:
    1. Explicit caller-provided ``path``.
    2. Machine-global runtime registry: ``settings.LOCAL_DATA_ROOT / "ml_models" / "registry.yaml"``
       if it exists.
    3. Repo-root fallback: ``_DEFAULT_REGISTRY_PATH`` (``ml/registry.yaml``).
    """
    if path is not None:
        return Path(path)
    try:
        from settings import settings  # noqa: PLC0415
        local_path = settings.LOCAL_DATA_ROOT / "ml_models" / "registry.yaml"
        if local_path.exists():
            return local_path
    except Exception:
        pass
    return _DEFAULT_REGISTRY_PATH


def load_registry(path: Optional[Path] = None) -> dict:
    """Load the registry YAML into a plain dict (empty dict on missing file)."""
    resolved = resolve_registry_path(path)
    if not resolved.exists():
        logger.warning("Registry file not found at %s — starting empty.", resolved)
        return {}
    with open(resolved, "r") as f:
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
    cpcv_mean_oos_sharpe: Optional[float] = None,
    cpcv_mean_oos_max_dd: Optional[float] = None,
) -> dict:
    """Update ``models.<model_key>.{trained_date,cpcv_dsr,pbo,n_train,deployable}``.

    The ``deployable`` flag is (re)derived from ``cpcv_dsr``/``pbo`` via
    :func:`compute_deployable` — callers do NOT pass it directly, so the gate
    can never be spoofed.

    Dual-persistence:
    When ``path`` is ``None``, writes to both ``settings.LOCAL_DATA_ROOT / "ml_models" / "registry.yaml"``
    (machine-global runtime state, immune to git branch/worktree switches) AND mirrors
    to the repo-root ``ml/registry.yaml`` (for git tracking/commits).
    When ``path`` is explicitly passed (e.g. unit tests), only writes to that target path.

    Returns the resulting model sub-dict.  Raises ``KeyError`` if the model key
    does not already exist in the registry (we update in place, never invent
    new roles).
    """
    if path is not None:
        target_paths = [Path(path)]
    else:
        target_paths = []
        try:
            from settings import settings  # noqa: PLC0415
            local_path = settings.LOCAL_DATA_ROOT / "ml_models" / "registry.yaml"
            target_paths.append(local_path)
        except Exception:
            pass
        if _DEFAULT_REGISTRY_PATH not in target_paths:
            target_paths.append(_DEFAULT_REGISTRY_PATH)

    data = load_registry(path)

    models = data.setdefault("models", {})
    if model_key not in models:
        raise KeyError(
            f"Model key '{model_key}' not found in registry. "
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
    entry["cpcv_mean_oos_sharpe"] = cpcv_mean_oos_sharpe
    entry["cpcv_mean_oos_max_dd"] = cpcv_mean_oos_max_dd

    for target in target_paths:
        try:
            _dump_registry(data, target)
        except Exception as exc:
            logger.warning("Failed to dump registry to %s: %s", target, exc)

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

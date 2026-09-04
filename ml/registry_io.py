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

from datetime import date, datetime
import logging
from pathlib import Path
from typing import Any, Optional

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
# owner:           team or person responsible for the model
# materiality_tier: experimental, non_material, or material
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
    """The single deployability gate: DSR > DSR_MIN AND PBO < PBO_MAX.

    Returns ``False`` whenever either metric is ``None`` (honest — an
    uncomputable metric can never clear the gate).
    """
    try:
        from validation.thresholds import DSR_MIN, PBO_MAX
    except Exception:
        DSR_MIN, PBO_MAX = 0.95, 0.50

    return (
        cpcv_dsr is not None
        and cpcv_dsr > DSR_MIN
        and pbo is not None
        and pbo < PBO_MAX
    )


def get_local_registry_path() -> Optional[Path]:
    """Return the machine-global registry path under LOCAL_DATA_ROOT, or None."""
    try:
        from settings import settings  # noqa: PLC0415
        return settings.LOCAL_DATA_ROOT / "ml_models" / "registry.yaml"
    except Exception:
        return None


def resolve_registry_path(path: Optional[Path] = None) -> Path:
    """Resolve the registry YAML path for single-path consumers.

    Priority:
    1. Explicit caller-provided ``path``.
    2. CWD test/custom ``ml/registry.yaml`` if running in an isolated non-repo CWD (e.g. pytest tmp_path).
    3. Machine-global runtime registry: ``settings.LOCAL_DATA_ROOT / "ml_models" / "registry.yaml"``
       if it exists.
    4. Repo-root fallback: ``_DEFAULT_REGISTRY_PATH`` (``ml/registry.yaml``).
    """
    if path is not None:
        return Path(path)

    try:
        is_in_repo = Path.cwd().resolve() == _DEFAULT_REGISTRY_PATH.parent.parent.resolve()
    except Exception:
        is_in_repo = True

    if not is_in_repo:
        return Path("ml/registry.yaml")

    local_path = get_local_registry_path()
    if local_path is not None and local_path.exists():
        return local_path
    return _DEFAULT_REGISTRY_PATH


def _parse_entry_date(value: Any) -> Optional[date]:
    """Helper to parse a model entry's trained_date into a date object."""
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


def _load_yaml_file(path: Path) -> dict:
    """Load and parse a YAML file into a dict, returning {} on missing/error."""
    try:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.debug("Failed to read registry YAML at %s: %s", path, exc)
        return {}


def load_registry(path: Optional[Path] = None) -> dict:
    """Load the registry YAML.

    When ``path`` is explicitly provided, loads that file directly.
    When ``path`` is ``None`` (default runtime):
    Performs a bidirectional smart-merge between the repo-tracked ``ml/registry.yaml``
    and the machine-global ``LOCAL_DATA_ROOT / ml_models / registry.yaml``.
    For each model, whichever source has the newer ``trained_date`` is preserved,
    preventing stale local files from shadowing fresh git pulls and preventing git
    checkouts from wiping fresh local retrains. If merged changes occur, synchronizes
    the local copy.
    """
    if path is not None:
        target = Path(path)
        if not target.exists():
            logger.warning("Registry file not found at %s — starting empty.", target)
            return {}
        return _load_yaml_file(target)

    try:
        is_in_repo = Path.cwd().resolve() == _DEFAULT_REGISTRY_PATH.parent.parent.resolve()
    except Exception:
        is_in_repo = True

    if not is_in_repo:
        resolved = resolve_registry_path()
        return _load_yaml_file(resolved)

    repo_data = _load_yaml_file(_DEFAULT_REGISTRY_PATH)
    local_path = get_local_registry_path()
    local_data = _load_yaml_file(local_path) if local_path is not None else {}

    if not repo_data and not local_data:
        return {}
    if not repo_data:
        return local_data
    if not local_data:
        return repo_data

    # Merge per-model:
    merged: dict[str, Any] = dict(repo_data)
    repo_models = dict(repo_data.get("models") or {})
    local_models = dict(local_data.get("models") or {})
    merged_models = dict(repo_models)

    if isinstance(local_models, dict):
        for model_key, local_spec in local_models.items():
            if not isinstance(local_spec, dict):
                continue
            if model_key not in merged_models:
                merged_models[model_key] = local_spec
            else:
                repo_spec = merged_models[model_key]
                d_local = _parse_entry_date(local_spec.get("trained_date"))
                d_repo = _parse_entry_date(repo_spec.get("trained_date")) if isinstance(repo_spec, dict) else None

                # Field-level merge, not a wholesale dict swap: whichever source
                # has the newer (or equal) trained_date wins on a per-key
                # conflict, but a key that exists ONLY in the older source
                # (e.g. owner/materiality_tier, which a bare machine-global
                # retrain run may never have populated) must still survive.
                # A prior wholesale-replace here silently dropped owner/
                # materiality_tier from the repo-tracked registry.yaml the
                # first time a model retrained with a newer-or-equal date
                # while the local registry.yaml lacked those fields --
                # see tests/test_registry_load.py::
                # test_load_registry_round_trips_owner_and_materiality_tier.
                base_spec = repo_spec if isinstance(repo_spec, dict) else {}
                if d_local is not None and (d_repo is None or d_local >= d_repo):
                    # Local has the newer (or equal) date: local's values win
                    # on conflicts, repo-only keys are preserved.
                    merged_entry = dict(base_spec)
                    merged_entry.update(local_spec)
                else:
                    # Repo has the newer date (e.g. freshly pulled commit):
                    # repo's values win on conflicts, local-only keys survive.
                    merged_entry = dict(local_spec)
                    merged_entry.update(base_spec)
                merged_models[model_key] = merged_entry

    merged["models"] = merged_models

    # Self-sync local file if it differed
    if local_path is not None and local_data != merged:
        try:
            _dump_registry(merged, local_path)
        except Exception as exc:
            logger.debug("Failed to sync merged registry to %s: %s", local_path, exc)

    return merged


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
    When ``path`` is explicitly passed (e.g. unit tests), only writes to that target path
    and propagates any write error.

    Returns the resulting model sub-dict.  Raises ``KeyError`` if the model key
    does not already exist in the registry (we update in place, never invent
    new roles).
    """
    fail_hard = False
    if path is not None:
        target_paths = [Path(path)]
        fail_hard = True
    else:
        target_paths = []
        local_path = get_local_registry_path()
        if local_path is not None:
            target_paths.append(local_path)
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

    success_count = 0
    last_exc = None
    for target in target_paths:
        try:
            _dump_registry(data, target)
            success_count += 1
        except Exception as exc:
            last_exc = exc
            logger.warning("Failed to dump registry to %s: %s", target, exc)
            if fail_hard:
                raise

    if success_count == 0:
        raise IOError(f"Failed to persist registry updates to any target path: {last_exc}") from last_exc

    logger.info(
        "Registry updated: %s trained_date=%s dsr=%s pbo=%s n_train=%s deployable=%s (written to %d targets)",
        model_key, trained_date, cpcv_dsr, pbo, n_train, entry["deployable"], success_count,
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
    with open(path, "w", encoding="utf-8") as f:
        f.write(_REGISTRY_HEADER)
        f.write("\n")
        f.write(body)

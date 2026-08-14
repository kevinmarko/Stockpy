# Implementation Plan: Sync ML Model Registry & Permanent LOCAL_DATA_ROOT Fix

## Summary
Anchor runtime ML registry resolution and writes to `settings.LOCAL_DATA_ROOT / "ml_models" / "registry.yaml"` with dual-persistence to `ml/registry.yaml`, plus self-healing artifact discovery in `pilots/models.py`.

## Changes Made
1. `ml/registry.yaml`: Updated model entries for `lgbm_ranker`, `meta_labeler_timeseries_momentum`, and `meta_labeler_cross_sectional_momentum` with honest `2026-08-14` training metrics.
2. `ml/registry_io.py`: Added `resolve_registry_path()` and updated `load_registry()` and `update_model_metrics()` for dual-persistence.
3. `pilots/models.py`: Added `_discover_latest_artifact_date()` for self-healing discovery against newer `.pkl` files on disk.
4. `tests/test_registry_load.py`: Added unit tests for path resolution and self-healing.
5. `tests/test_pilots_api.py`: Wrapped `TestModelsRegistry` in `STATE_API_TOKEN` mock.
6. `docs/architecture/data-layer.md` & `docs/architecture/ml-and-reports.md`: Updated architecture documentation.

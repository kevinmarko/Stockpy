# Walkthrough: Sync ML Model Registry & Permanent LOCAL_DATA_ROOT Fix

## Summary
Successfully synchronized `ml/registry.yaml` with the `2026-08-14` training artifacts and implemented machine-global runtime persistence via `LOCAL_DATA_ROOT` + self-healing artifact discovery.

## Changes
- `ml/registry.yaml`: Updated `2026-08-14` training metrics.
- `ml/registry_io.py`: Added `resolve_registry_path()` and dual-persistence.
- `pilots/models.py`: Self-healing discovery against `~/.stockpy_local/ml_models/`.
- `tests/test_registry_load.py`: Added resolution and self-healing tests.
- `tests/test_pilots_api.py`: Updated `TestModelsRegistry`.
- `docs/architecture/*.md`: Updated documentation.

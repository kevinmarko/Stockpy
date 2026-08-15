# Walkthrough: Sync ML Model Registry & Comprehensive LOCAL_DATA_ROOT Dual-Persistence Fix

## Summary
Resolved all 10 code review findings regarding model registry persistence, bidirectional synchronization between git and `LOCAL_DATA_ROOT`, single-pass artifact discovery, metric honesty invariants, and reader unification.

## Key Changes
1. **`scripts/train_meta_labelers.py`**:
   - `_update_registry_row` now passes `path=None` on unoverridden production runs to dual-write to `LOCAL_DATA_ROOT` and repo `ml/registry.yaml`.
2. **`ml/registry_io.py`**:
   - Implemented `get_local_registry_path()` and `resolve_registry_path()`.
   - Implemented bidirectional smart-merge in `load_registry()`: compares `trained_date` per model so that fresh Git commits are never shadowed by stale local files and fresh local training is never wiped by Git checkouts.
   - Updated `update_model_metrics()` to propagate I/O errors immediately for explicit paths and raise `IOError` if all dual-write targets fail.
3. **`pilots/models.py`**:
   - Implemented single-pass `_scan_local_artifacts()` for `.pkl` discovery.
   - Preserved metric consistency (CONSTRAINT #4): unvalidated new `.pkl` files on disk reset metrics to `None` and `deployable=False` rather than pairing with stale metrics.
4. **Readers Unified**:
   - `gui/panels/analytics_signals.py`: updated `_load_registry_rows` to use `resolve_registry_path()`.
   - `gui/panels/analytics.py`: updated `_load_ml_registry_rows` to use `resolve_registry_path()`.
   - `investyo_mcp_server.py`: updated `get_model_registry_status` to use `load_registry()`.
5. **Unit Tests**:
   - `tests/test_registry_load.py`: tests for path resolution, smart merge, unvalidated metric resets, and error propagation.
   - `tests/test_train_meta_labelers.py`: hermetic regression test for `_update_registry_row` default dual-write.
   - `tests/test_investyo_mcp_server.py`: verified `TestGetModelRegistryStatus` passes.

## Verification
- All targeted pytest suites pass cleanly (`tests/test_registry_load.py`, `tests/test_train_lgbm.py`, `tests/test_train_meta_labelers.py`, `tests/test_investyo_mcp_server.py`, `tests/test_pilots_api.py`).
- `npm run --prefix webapp typecheck` passes with 0 errors.

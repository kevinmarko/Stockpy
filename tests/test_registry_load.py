"""
tests/test_registry_load.py
================================
Verifies that the ml/registry.yaml round-trips and that model metadata is
consistently structured (Prompt 4.3 — qlib-style architecture audit).

These tests do NOT attempt to load real persisted model pickles (those only
exist after training runs). They validate:
1. The YAML is parseable and has the required schema.
2. Fields are the right types (path strings, null or float metrics, bool flag).
3. The StrategySpec + PITFeatureStore classes are importable and correct.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml


_REGISTRY_PATH = Path(__file__).parent.parent / "ml" / "registry.yaml"
_REQUIRED_MODEL_FIELDS = {"role", "path", "trained_date", "cpcv_dsr", "pbo", "deployable", "notes"}


# ---------------------------------------------------------------------------
# Test 1: YAML is parseable
# ---------------------------------------------------------------------------

def test_registry_yaml_parseable():
    assert _REGISTRY_PATH.exists(), f"ml/registry.yaml not found at {_REGISTRY_PATH}"
    with open(_REGISTRY_PATH) as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "registry.yaml must be a YAML mapping"
    assert "models" in data, "registry.yaml must have a top-level 'models' key"


# ---------------------------------------------------------------------------
# Test 2: Each model has required fields
# ---------------------------------------------------------------------------

def test_registry_models_have_required_fields():
    with open(_REGISTRY_PATH) as f:
        data = yaml.safe_load(f)
    models = data["models"]
    assert isinstance(models, dict) and len(models) > 0, "registry.yaml has no models"

    for model_id, spec in models.items():
        missing = _REQUIRED_MODEL_FIELDS - set(spec.keys())
        assert not missing, f"Model '{model_id}' missing fields: {missing}"


# ---------------------------------------------------------------------------
# Test 3: deployable flag is a boolean
# ---------------------------------------------------------------------------

def test_registry_deployable_is_bool():
    with open(_REGISTRY_PATH) as f:
        data = yaml.safe_load(f)
    for model_id, spec in data["models"].items():
        assert isinstance(spec["deployable"], bool), (
            f"Model '{model_id}' deployable field must be a bool, got {type(spec['deployable'])}"
        )


# ---------------------------------------------------------------------------
# Test 4: cpcv_dsr and pbo are either null or in valid ranges
# ---------------------------------------------------------------------------

def test_registry_metric_ranges():
    with open(_REGISTRY_PATH) as f:
        data = yaml.safe_load(f)
    for model_id, spec in data["models"].items():
        dsr = spec.get("cpcv_dsr")
        pbo = spec.get("pbo")
        if dsr is not None:
            assert 0.0 <= dsr <= 5.0, f"cpcv_dsr for '{model_id}' is out of plausible range: {dsr}"
        if pbo is not None:
            assert 0.0 <= pbo <= 1.0, f"pbo for '{model_id}' must be in [0,1]: {pbo}"


# ---------------------------------------------------------------------------
# Test 4b: optional provenance fields are correctly typed when present
# ---------------------------------------------------------------------------

def test_registry_provenance_fields_typed_when_present():
    """The optional provenance fields (artifact_file, hyperparameters,
    train_window, features), when present on a row, are the expected types
    (str / dict / dict / list) or None. Absent fields are tolerated (the
    fields are backward-compatible additions)."""
    with open(_REGISTRY_PATH) as f:
        data = yaml.safe_load(f)
    for model_id, spec in data["models"].items():
        if "artifact_file" in spec and spec["artifact_file"] is not None:
            assert isinstance(spec["artifact_file"], str), (
                f"artifact_file for '{model_id}' must be a str or None"
            )
        if "hyperparameters" in spec and spec["hyperparameters"] is not None:
            assert isinstance(spec["hyperparameters"], dict), (
                f"hyperparameters for '{model_id}' must be a dict or None"
            )
        if "train_window" in spec and spec["train_window"] is not None:
            assert isinstance(spec["train_window"], dict), (
                f"train_window for '{model_id}' must be a dict or None"
            )
        if "features" in spec and spec["features"] is not None:
            assert isinstance(spec["features"], list), (
                f"features for '{model_id}' must be a list or None"
            )


def test_provenance_never_affects_deployable():
    """A row with rich provenance but failing metrics is still NOT deployable —
    provenance is decoupled from the DSR/PBO gate."""
    from ml import registry_io

    # Bad metrics (DSR too low) despite rich provenance → not deployable.
    assert registry_io.compute_deployable(0.10, 0.10) is False
    # The gate only reads DSR/PBO; provenance args aren't even accepted by it.


# ---------------------------------------------------------------------------
# Test 5: PITFeatureStore round-trip (in-memory cache)
# ---------------------------------------------------------------------------

def test_pit_feature_store_write_read():
    import tempfile
    from ml.data.store import PITFeatureStore

    feat = pd.DataFrame(
        {"feature_A": [0.1, 0.5, 0.9], "feature_B": [1.0, 2.0, 3.0]},
        index=["AAPL", "MSFT", "JNJ"],
    )
    feat.index.name = "ticker"
    as_of = pd.Timestamp("2022-06-15")

    with tempfile.TemporaryDirectory() as tmpdir:
        store = PITFeatureStore(cache_dir=tmpdir)
        store.write(as_of, feat)

        dates = store.available_dates()
        assert len(dates) == 1
        assert dates[0] == as_of

        panel = store.read_range("2022-01-01", "2022-12-31")
        assert not panel.empty
        assert "feature_A" in panel.columns
        assert len(panel) == 3  # 3 tickers


# ---------------------------------------------------------------------------
# Test 6: MetaLabelerRegistry round-trips (register / has / get_proba)
# ---------------------------------------------------------------------------

def test_meta_labeler_registry_neutral_default():
    """get_proba returns 1.0 when no labeler is registered for that signal."""
    from ml.meta_labeling import MetaLabelerRegistry
    import pandas as pd

    registry = MetaLabelerRegistry()
    feat = pd.DataFrame({"f": [0.5]})
    assert registry.get_proba("nonexistent_signal", feat) == 1.0


def test_meta_labeler_registry_register_has():
    from ml.meta_labeling import MetaLabelerRegistry, MetaLabeler

    registry = MetaLabelerRegistry()
    labeler = MetaLabeler(signal_id="ts_momentum")
    registry.register(labeler)

    assert registry.has("ts_momentum")
    assert not registry.has("cross_sectional_momentum")

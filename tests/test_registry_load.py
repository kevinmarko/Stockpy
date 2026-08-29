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
_REQUIRED_MODEL_FIELDS = {"role", "owner", "materiality_tier", "path", "trained_date", "cpcv_dsr", "pbo", "deployable", "notes"}


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


def test_high_dsr_high_pbo_still_not_deployable():
    """Regression lock for the real `cross-sectional-momentum` pilot case
    (2026-08 deployability-gate audit): Sharpe 1.00, DSR 1.00 — an
    excellent-looking, clearly-passing DSR — but PBO 0.73 (73% probability
    of backtest overfitting), which correctly overrides the great DSR and
    keeps the strategy non-deployable.

    This pins down that PBO is a hard veto, not something a high DSR (or a
    high in-sample Sharpe) can outweigh — the exact property someone
    "fixing" a non-deployable pilot by loosening PBO_MAX without
    understanding why it's there would break silently otherwise.
    """
    from ml import registry_io

    assert registry_io.compute_deployable(1.00, 0.73) is False


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


# ---------------------------------------------------------------------------
# Test 7: Registry path resolution & LOCAL_DATA_ROOT priorities
# ---------------------------------------------------------------------------

def test_resolve_registry_path_priorities(tmp_path, monkeypatch):
    from settings import settings
    from ml.registry_io import resolve_registry_path, _DEFAULT_REGISTRY_PATH

    # Priority 1: Explicit path
    custom = tmp_path / "custom.yaml"
    assert resolve_registry_path(custom) == custom

    # Priority 2: LOCAL_DATA_ROOT / ml_models / registry.yaml if it exists
    fake_local = tmp_path / "stockpy_local"
    local_reg = fake_local / "ml_models" / "registry.yaml"
    local_reg.parent.mkdir(parents=True)
    local_reg.write_text("models: {}\n", encoding="utf-8")

    monkeypatch.setattr(settings, "LOCAL_DATA_ROOT", fake_local)
    assert resolve_registry_path() == local_reg

    # Priority 3: Fallback to _DEFAULT_REGISTRY_PATH if local doesn't exist
    local_reg.unlink()
    assert resolve_registry_path() == _DEFAULT_REGISTRY_PATH


def test_model_registry_rows_self_healing(tmp_path, monkeypatch):
    """Self-healing discovery: if a newer dated .pkl exists in LOCAL_DATA_ROOT/ml_models,
    model_registry_rows surfaces the real artifact date and calculates freshness."""
    from ml import registry_io
    from pilots.models import model_registry_rows
    from settings import settings

    fake_local = tmp_path / "stockpy_local"
    models_dir = fake_local / "ml_models"
    models_dir.mkdir(parents=True)

    # Write a registry with an older date
    reg_file = models_dir / "registry.yaml"
    reg_file.write_text("""
models:
  lgbm_ranker:
    role: cross_sectional_ranker
    path: ml/models/lgbm_latest.pkl
    trained_date: '2026-08-01'
    cpcv_dsr: 0.99
    pbo: 0.2
    n_train: 400
    deployable: true
    notes: Test note
    artifact_file: lgbm_20260801.pkl
""", encoding="utf-8")

    # Create a newer physical artifact on disk with matching artifact_file
    (models_dir / "lgbm_20260814.pkl").write_text("binary", encoding="utf-8")

    monkeypatch.setattr(settings, "LOCAL_DATA_ROOT", fake_local)
    # Isolate from the real, git-tracked ml/registry.yaml (its trained_date changes
    # over time as models are retrained, which would otherwise win the smart-merge
    # against this test's local fixture whenever it's newer). Intercept the repo-side
    # read (rather than repointing _DEFAULT_REGISTRY_PATH itself, which would also
    # flip load_registry's is_in_repo check and silently fall back to re-reading the
    # real file via a hardcoded relative path) so lgbm_ranker is absent from repo_data
    # and this test only ever exercises local self-healing.
    _real_load_yaml_file = registry_io._load_yaml_file

    def _fake_load_yaml_file(path):
        if path == registry_io._DEFAULT_REGISTRY_PATH:
            return {"models": {}}
        return _real_load_yaml_file(path)

    monkeypatch.setattr(registry_io, "_load_yaml_file", _fake_load_yaml_file)

    rows = model_registry_rows()
    row = next(r for r in rows if r["name"] == "lgbm_ranker")
    # Self-healed to the newer physical artifact date
    assert row["trained_date"] == "2026-08-14"


def test_model_registry_rows_unvalidated_artifact_resets_metrics(tmp_path, monkeypatch):
    """If a new .pkl exists on disk whose filename does not match the registry's validated
    artifact_file, the row surfaces the new date but resets metrics to None (CONSTRAINT #4)."""
    from ml import registry_io
    from pilots.models import model_registry_rows
    from settings import settings

    fake_local = tmp_path / "stockpy_local"
    models_dir = fake_local / "ml_models"
    models_dir.mkdir(parents=True)

    reg_file = models_dir / "registry.yaml"
    reg_file.write_text("""
models:
  lgbm_ranker:
    role: cross_sectional_ranker
    path: ml/models/lgbm_latest.pkl
    trained_date: '2026-08-01'
    cpcv_dsr: 0.99
    pbo: 0.2
    n_train: 400
    deployable: true
    notes: Test note
    artifact_file: lgbm_20260801.pkl
""", encoding="utf-8")

    # New artifact on disk with different filename/date
    (models_dir / "lgbm_20260815.pkl").write_text("binary", encoding="utf-8")
    monkeypatch.setattr(settings, "LOCAL_DATA_ROOT", fake_local)
    # Isolate from the real, git-tracked ml/registry.yaml (see comment in
    # test_model_registry_rows_self_healing above).
    _real_load_yaml_file = registry_io._load_yaml_file

    def _fake_load_yaml_file(path):
        if path == registry_io._DEFAULT_REGISTRY_PATH:
            return {"models": {}}
        return _real_load_yaml_file(path)

    monkeypatch.setattr(registry_io, "_load_yaml_file", _fake_load_yaml_file)

    rows = model_registry_rows()
    row = next(r for r in rows if r["name"] == "lgbm_ranker")
    assert row["trained_date"] == "2026-08-15"
    assert row["cpcv_dsr"] is None
    assert row["pbo"] is None
    assert row["deployable"] is False


def test_load_registry_smart_merge_git_newer(tmp_path, monkeypatch):
    """When git-tracked registry has a newer model than LOCAL_DATA_ROOT, git entry wins."""
    from ml import registry_io
    from ml.registry_io import load_registry
    from settings import settings

    fake_local = tmp_path / "stockpy_local"
    models_dir = fake_local / "ml_models"
    models_dir.mkdir(parents=True)

    # Local has an older model
    local_reg = models_dir / "registry.yaml"
    local_reg.write_text("""
models:
  lgbm_ranker:
    role: cross_sectional_ranker
    path: ml/models/lgbm_latest.pkl
    trained_date: '2026-08-01'
    cpcv_dsr: 0.90
    pbo: 0.4
    n_train: 300
    deployable: false
""", encoding="utf-8")

    monkeypatch.setattr(settings, "LOCAL_DATA_ROOT", fake_local)

    # Use an explicit, self-contained "git" fixture instead of the real, evolving
    # ml/registry.yaml — that file's trained_date changes every retrain and this
    # test only needs to prove "the newer of the two dates wins the merge". Intercept
    # the repo-side read rather than repointing _DEFAULT_REGISTRY_PATH itself, which
    # would also flip load_registry's is_in_repo check and silently fall back to
    # re-reading the real file via a hardcoded relative path (see comment in
    # test_model_registry_rows_self_healing above).
    fake_repo_data = {
        "models": {
            "lgbm_ranker": {
                "role": "cross_sectional_ranker",
                "path": "ml/models/lgbm_latest.pkl",
                "trained_date": "2026-08-14",
                "cpcv_dsr": 0.99,
                "pbo": 0.2,
                "n_train": 460,
                "deployable": True,
            }
        }
    }
    _real_load_yaml_file = registry_io._load_yaml_file

    def _fake_load_yaml_file(path):
        if path == registry_io._DEFAULT_REGISTRY_PATH:
            return fake_repo_data
        return _real_load_yaml_file(path)

    monkeypatch.setattr(registry_io, "_load_yaml_file", _fake_load_yaml_file)

    data = load_registry()
    assert "models" in data
    assert data["models"]["lgbm_ranker"]["trained_date"] == "2026-08-14"
    assert data["models"]["lgbm_ranker"]["deployable"] is True


def test_load_registry_smart_merge_local_newer(tmp_path, monkeypatch):
    """When LOCAL_DATA_ROOT has a newer retrained model than git, local entry wins."""
    from ml import registry_io
    from ml.registry_io import load_registry
    from settings import settings

    fake_local = tmp_path / "stockpy_local"
    models_dir = fake_local / "ml_models"
    models_dir.mkdir(parents=True)

    # Local has a freshly retrained model with a newer date than the fake "git" fixture below.
    local_reg = models_dir / "registry.yaml"
    local_reg.write_text("""
models:
  lgbm_ranker:
    role: cross_sectional_ranker
    path: ml/models/lgbm_latest.pkl
    trained_date: '2026-09-01'
    cpcv_dsr: 0.999
    pbo: 0.1
    n_train: 800
    deployable: true
""", encoding="utf-8")

    monkeypatch.setattr(settings, "LOCAL_DATA_ROOT", fake_local)

    # Explicit, self-contained "git" fixture — see test_load_registry_smart_merge_git_newer
    # for why this must not depend on the real, evolving ml/registry.yaml.
    fake_repo_data = {
        "models": {
            "lgbm_ranker": {
                "role": "cross_sectional_ranker",
                "path": "ml/models/lgbm_latest.pkl",
                "trained_date": "2026-08-14",
                "cpcv_dsr": 0.99,
                "pbo": 0.2,
                "n_train": 460,
                "deployable": True,
            }
        }
    }
    _real_load_yaml_file = registry_io._load_yaml_file

    def _fake_load_yaml_file(path):
        if path == registry_io._DEFAULT_REGISTRY_PATH:
            return fake_repo_data
        return _real_load_yaml_file(path)

    monkeypatch.setattr(registry_io, "_load_yaml_file", _fake_load_yaml_file)

    data = load_registry()
    assert "models" in data
    assert data["models"]["lgbm_ranker"]["trained_date"] == "2026-09-01"
    assert data["models"]["lgbm_ranker"]["cpcv_dsr"] == 0.999


def test_update_model_metrics_explicit_path_error_propagates(tmp_path, monkeypatch):
    """When an explicit unwriteable path is passed, update_model_metrics propagates the write error."""
    import ml.registry_io as reg_io

    valid_reg = tmp_path / "reg.yaml"
    valid_reg.write_text("models:\n  lgbm_ranker:\n    role: test\n    trained_date: '2026-08-01'\n", encoding="utf-8")

    # Mock _dump_registry to simulate a filesystem write failure
    def _fail_dump(*args, **kwargs):
        raise PermissionError("Simulated read-only filesystem")

    monkeypatch.setattr(reg_io, "_dump_registry", _fail_dump)

    with pytest.raises(PermissionError):
        reg_io.update_model_metrics("lgbm_ranker", path=valid_reg, trained_date="2026-08-15")


def test_load_registry_round_trips_owner_and_materiality_tier(tmp_path, monkeypatch):
    from ml.registry_io import load_registry, update_model_metrics
    from settings import settings

    fake_local = tmp_path / "stockpy_local"
    models_dir = fake_local / "ml_models"
    models_dir.mkdir(parents=True)
    monkeypatch.setattr(settings, "LOCAL_DATA_ROOT", fake_local)

    data = load_registry()
    for model_id, spec in data["models"].items():
        assert "owner" in spec, f"owner missing in {model_id}"
        assert "materiality_tier" in spec, f"materiality_tier missing in {model_id}"
        
    # Test that update_model_metrics doesn't drop them
    valid_reg = tmp_path / "reg.yaml"
    valid_reg.write_text("models:\n  test_model:\n    role: test\n    owner: TBD\n    materiality_tier: experimental\n    trained_date: '2026-08-01'\n", encoding="utf-8")
    
    updated = update_model_metrics("test_model", path=valid_reg, trained_date="2026-08-15")
    assert "owner" in updated
    assert updated["owner"] == "TBD"
    assert "materiality_tier" in updated
    assert updated["materiality_tier"] == "experimental"

    # Reload to ensure it round-tripped to disk
    reloaded = load_registry(path=valid_reg)
    assert reloaded["models"]["test_model"]["owner"] == "TBD"
    assert reloaded["models"]["test_model"]["materiality_tier"] == "experimental"


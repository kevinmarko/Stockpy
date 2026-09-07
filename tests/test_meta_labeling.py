import tempfile
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier

from ml.meta_labeling import MetaLabeler, _MODELS_DIR

def test_from_fitted_wraps_pre_fitted_classifier():
    """Ensure from_fitted wraps a pre-fitted classifier correctly."""
    clf = RandomForestClassifier(n_estimators=10, random_state=42)
    # mock a fitted state without actual fit
    clf.classes_ = np.array([0, 1])
    
    labeler = MetaLabeler.from_fitted(
        signal_id="test_signal",
        model=clf,
        feature_names=["f1", "f2"],
        n_train_samples=100,
        trained_at=datetime(2025, 1, 1)
    )
    
    assert labeler.signal_id == "test_signal"
    assert labeler._model is clf
    assert labeler._feature_names == ["f1", "f2"]
    assert labeler._n_train_samples == 100
    assert labeler._last_trained == datetime(2025, 1, 1)
    
def test_load_latest_namespace_isolation(monkeypatch):
    """load_latest default-prefix behavior is unchanged; custom-prefix calls never cross-match the default namespace (the collision-fix regression test)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        monkeypatch.setattr("ml.meta_labeling._MODELS_DIR", tmp_path)
        
        # Create an old-style AFML pickle (prefix="meta")
        afml_path = tmp_path / "meta_test_signal_20250101_120000.pkl"
        
        # Create a new-style backfill pickle (prefix="backfill_meta")
        backfill_path = tmp_path / "backfill_meta_test_signal_20250101_130000.pkl"
        
        # Save a dummy object
        labeler_afml = MetaLabeler(signal_id="test_signal")
        labeler_afml._model = RandomForestClassifier()
        labeler_afml._feature_names = ["f1"]
        labeler_afml._n_train_samples = 100
        labeler_afml._last_trained = datetime(2025, 1, 1)
        labeler_afml.save(afml_path)
        
        labeler_bf = MetaLabeler(signal_id="test_signal")
        labeler_bf._model = RandomForestClassifier()
        labeler_bf._feature_names = ["f2"]
        labeler_bf._n_train_samples = 200
        labeler_bf._last_trained = datetime(2025, 1, 1)
        labeler_bf.save(backfill_path)
        
        # default prefix is "meta", should only load afml
        loaded_default = MetaLabeler.load_latest("test_signal")
        assert loaded_default is not None
        assert loaded_default._feature_names == ["f1"]
        
        # custom prefix is "backfill_meta", should only load backfill
        loaded_custom = MetaLabeler.load_latest("test_signal", prefix="backfill_meta")
        assert loaded_custom is not None
        assert loaded_custom._feature_names == ["f2"]

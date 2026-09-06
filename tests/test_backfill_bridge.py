import pytest
from pathlib import Path
from ml.meta_bootstrap import bootstrap_meta_registry
from ml.meta_labeling import MetaLabeler, global_meta_registry
from ml.registry_io import load_registry

@pytest.fixture(autouse=True)
def reset_registry():
    global_meta_registry._labelers.clear()

def test_bootstrap_backfill_bridge_disabled_by_default(monkeypatch, tmp_path):
    pass

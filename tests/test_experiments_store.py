import pytest
from datetime import datetime
from experiments.store import ExperimentStore

@pytest.fixture
def store():
    # Use in-memory SQLite for testing
    return ExperimentStore(db_url="sqlite:///:memory:")

def test_record_run(store):
    store.record_run("exp-1", "2026-08-23", "AAPL", "control")
    with store.Session() as session:
        from experiments.store import ExperimentRun
        runs = session.query(ExperimentRun).all()
        assert len(runs) == 1
        assert runs[0].experiment_id == "exp-1"
        assert runs[0].symbol == "AAPL"
        assert runs[0].assigned_arm == "control"

def test_record_observation(store):
    store.record_observation("exp-2", "2026-08-23", "MSFT", "treatment", {"action": "buy"}, 100.0)
    with store.Session() as session:
        from experiments.store import ExperimentObservation
        obs = session.query(ExperimentObservation).all()
        assert len(obs) == 1
        assert obs[0].experiment_id == "exp-2"
        assert obs[0].symbol == "MSFT"
        assert obs[0].arm == "treatment"
        assert obs[0].decision == {"action": "buy"}
        assert obs[0].size == 100.0

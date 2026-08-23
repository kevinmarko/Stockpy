
from experiments.compare import compare_arms
import pandas as pd

def test_insufficient_data():
    arm_returns = {
        "control": [0.01, -0.01, 0.02],
        "treatment": [0.02, 0.01]
    }
    res = compare_arms("exp_1", arm_returns, min_samples_per_arm=30)
    assert res["verdict"] == "insufficient_data"
    assert "required" in res
    assert res["n_per_arm"] == {"control": 3, "treatment": 2}

def test_sufficient_data(monkeypatch):
    def mock_dsr(*args, **kwargs):
        class MockRes:
            def __init__(self, sid):
                self.strategy_id = sid
                self.dsr_family_corrected = 1.0
        return [MockRes("control"), MockRes("treatment")]
    
    import validation.multiple_testing
    monkeypatch.setattr(validation.multiple_testing, "deflated_sharpe_family", mock_dsr)
    
    arm_returns = {
        "control": [0.01]*30,
        "treatment": [0.02]*30
    }
    res = compare_arms("exp_1", arm_returns, min_samples_per_arm=30)
    
    assert res["verdict"] == "completed"
    assert "metrics" in res
    assert len(res["dsr_family"]) == 2

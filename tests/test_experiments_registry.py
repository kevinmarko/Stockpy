from datetime import datetime
from experiments.registry import Experiment, Arm

def test_experiment_creation():
    arm1 = Arm(name="control", overrides={})
    arm2 = Arm(name="treatment", overrides={"feature_x": 1.5})
    exp = Experiment(
        id="exp-123",
        name="Test Experiment",
        unit="model_variant",
        arms=[arm1, arm2],
        allocation=[0.5, 0.5],
        started_at=datetime(2026, 1, 1),
        min_samples_per_arm=30
    )
    
    assert exp.id == "exp-123"
    assert exp.unit == "model_variant"
    assert len(exp.arms) == 2
    assert exp.arms[0].name == "control"
    assert exp.arms[1].overrides == {"feature_x": 1.5}
    assert exp.status == "running"

from dataclasses import dataclass, field
from typing import Dict, Literal, List, Optional
from datetime import datetime

UnitType = Literal["signal_weights", "pilot_params", "sizing_params", "model_variant"]
StatusType = Literal["running", "stopped"]

@dataclass
class Arm:
    name: str
    overrides: Dict[str, float]

@dataclass
class Experiment:
    id: str
    name: str
    unit: UnitType
    arms: List[Arm]
    allocation: List[float]
    started_at: datetime
    min_samples_per_arm: int
    status: StatusType = "running"

    def __post_init__(self):
        if not self.arms:
            raise ValueError("Experiment must have at least one arm.")
        if self.arms[0].overrides:
            raise ValueError("The first arm (control) must have empty overrides.")
        if len(self.arms) != len(self.allocation):
            raise ValueError("Number of arms must match length of allocation list.")
        if sum(self.allocation) != 1.0 and sum(self.allocation) != 100:
            raise ValueError("Allocations must sum to 1.0 or 100.")

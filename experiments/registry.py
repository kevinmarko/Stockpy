from dataclasses import dataclass, field
from typing import Dict, Literal, List, Optional
from datetime import datetime

UnitType = Literal["signal_weights", "pilot_params", "sizing_params", "model_variant"]
StatusType = Literal["running", "stopped", "insufficient_data"]

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

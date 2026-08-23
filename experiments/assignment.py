import hashlib
from typing import List

def assign_arm(experiment_id: str, symbol: str, cycle_date: str, arms: List[str], allocations: List[float]) -> str:
    """Deterministic assignment seeded by hash(experiment_id, symbol, cycle_date)."""
    assert len(arms) == len(allocations)
    assert abs(sum(allocations) - 1.0) < 1e-6
    
    seed = f"{experiment_id}_{symbol}_{cycle_date}".encode("utf-8")
    hash_val = int(hashlib.md5(seed).hexdigest(), 16)
    # Map to [0, 1)
    normalized = (hash_val % 1000000) / 1000000.0
    
    cumulative = 0.0
    for arm, alloc in zip(arms, allocations):
        cumulative += alloc
        if normalized < cumulative:
            return arm
    return arms[-1]

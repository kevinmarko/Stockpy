
import pandas as pd
from typing import Dict, Any, List, Sequence
from validation.metrics import sharpe_ratio, deflated_sharpe_ratio, probability_of_backtest_overfitting
from validation.stress_scenarios import compute_max_drawdown
from validation.multiple_testing import deflated_sharpe_family

def compare_arms(experiment_id: str, arm_returns: Dict[str, Sequence[float]], min_samples_per_arm: int) -> Dict[str, Any]:
    n_per_arm = {arm: len(rets) for arm, rets in arm_returns.items()}
    
    if len(n_per_arm) < 2 or any(n < min_samples_per_arm for n in n_per_arm.values()):
        return {
            "verdict": "insufficient_data",
            "n_per_arm": n_per_arm,
            "required": min_samples_per_arm,
            "reason": "One or more arms have insufficient samples."
        }
    
    arm_metrics = {}
    for arm, rets in arm_returns.items():
        ret_series = pd.Series(rets)
        arm_metrics[arm] = {
            "sharpe_ratio": float(sharpe_ratio(ret_series)),
            "max_drawdown": float(compute_max_drawdown(ret_series))
        }
        
    arm_names = list(arm_returns.keys())
    
    dsr_family_result = deflated_sharpe_family(
        sharpe_ratios=[arm_metrics[arm]["sharpe_ratio"] for arm in arm_names],
        n_trials_per_strategy=[n_per_arm[arm] for arm in arm_names],
        strategy_ids=arm_names
    )
    
    return {
        "verdict": "completed",
        "n_per_arm": n_per_arm,
        "required": min_samples_per_arm,
        "metrics": arm_metrics,
        "dsr_family": [
            {
                "arm": r.strategy_id, 
                "dsr": r.dsr_family_corrected
            } for r in dsr_family_result
        ]
    }

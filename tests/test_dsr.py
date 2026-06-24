import numpy as np
import pytest
from validation.metrics import deflated_sharpe_ratio

def test_bailey_lopez_de_prado_worked_example():
    """
    Verify that our DSR implementation matches Bailey & Lopez de Prado's worked 
    example from the SSRN paper appendix:
    - Annualized SR_observed = 2.5
    - Annualized SR_variance = 0.5
    - n_trials = 100
    - n_observations = 1250 (daily, freq=252)
    - skew = -3.0
    - kurtosis = 10.0 (non-excess)
    
    The expected DSR is approximately 0.90 (within 0.01 tolerance).
    """
    dsr = deflated_sharpe_ratio(
        sr_observed=2.5,
        n_trials=100,
        sr_variance=0.5,
        skew=-3.0,
        kurtosis=10.0,
        n_observations=1250,
        freq=252
    )
    
    # Assert that DSR is within 0.01 of 0.90 (0.89 to 0.91)
    assert abs(dsr - 0.90) <= 0.01

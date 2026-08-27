import pytest
import numpy as np
import pandas as pd
from validation.covariate_drift import (
    compute_psi,
    PSIResult,
    adapt_symbol_history_to_windows,
    check_and_alert_feature_drift,
    PSI_ALERT_THRESHOLD
)

def test_compute_psi_identical_distributions():
    np.random.seed(42)
    ref = pd.Series(np.random.normal(0, 1, 1000))
    curr = pd.Series(np.random.normal(0, 1, 100))
    psi = compute_psi(ref, curr)
    assert psi < PSI_ALERT_THRESHOLD

def test_compute_psi_shifted_distributions():
    np.random.seed(42)
    ref = pd.Series(np.random.normal(0, 1, 1000))
    curr = pd.Series(np.random.normal(3, 1, 100)) # Significant mean shift
    psi = compute_psi(ref, curr)
    assert psi >= PSI_ALERT_THRESHOLD

def test_compute_psi_degrades_gracefully():
    # Empty series
    assert compute_psi(pd.Series(dtype=float), pd.Series(dtype=float)) == 0.0
    
    # Single value
    ref = pd.Series([1.0, 1.0, 1.0])
    curr = pd.Series([1.0, 1.0])
    assert compute_psi(ref, curr) == 0.0
    
    # Introduce variance to zero-variance reference
    curr_diff = pd.Series([1.0, 2.0])
    assert np.isinf(compute_psi(ref, curr_diff))

def test_adapt_symbol_history_to_windows():
    df = pd.DataFrame({'feature_a': range(100)})
    ref, curr = adapt_symbol_history_to_windows(df, 'feature_a', reference_size=60, recent_size=20)
    assert len(ref) == 60
    assert len(curr) == 20
    assert list(ref) == list(range(20, 80))
    assert list(curr) == list(range(80, 100))
    
    # Too short
    df_short = pd.DataFrame({'feature_a': range(10)})
    ref_short, curr_short = adapt_symbol_history_to_windows(df_short, 'feature_a')
    assert len(ref_short) == 0
    assert len(curr_short) == 0

def test_check_and_alert_feature_drift():
    np.random.seed(42)
    df = pd.DataFrame({
        'stable_feature': np.random.normal(0, 1, 1000),
        'drifting_feature': np.concatenate([np.random.normal(0, 1, 980), np.random.normal(5, 1, 20)])
    })
    
    alerts = []
    def alert_fn(msg):
        alerts.append(msg)
        
    results = check_and_alert_feature_drift(df, ['stable_feature', 'drifting_feature'], alert_fn)
    
    assert len(results) == 2
    
    stable_res = next(r for r in results if r.feature == 'stable_feature')
    assert not stable_res.drift_detected
    assert stable_res.psi < PSI_ALERT_THRESHOLD
    
    drifting_res = next(r for r in results if r.feature == 'drifting_feature')
    assert drifting_res.drift_detected
    assert drifting_res.psi >= PSI_ALERT_THRESHOLD
    
    assert len(alerts) == 1
    assert "drifting_feature" in alerts[0]

import pandas as pd
import numpy as np
from typing import Callable, Any, Union, Optional


def make_synthetic_ohlcv(periods: int, seed: Optional[int] = None, end: str = "2026-06-24") -> pd.DataFrame:
    """Deterministic synthetic OHLCV history via a random walk on Close.

    Shared by the lookahead perturbation test files (previously duplicated
    near-identically across tests/test_indicators_lookahead.py and
    tests/test_processing_engine_lookahead.py, differing only in `periods`
    and RNG seed). Uses a local `np.random.RandomState(seed)` rather than
    the global `np.random.seed()` + bare `np.random.*` calls the original
    fixtures used, so callers get the same reproducibility guarantee
    (same seed -> same DataFrame) without depending on global RNG state
    or call order relative to other tests.
    """
    rng = np.random.RandomState(seed)
    dates = pd.date_range(end=end, periods=periods)
    close = 100.0 + np.cumsum(rng.normal(0, 1.0, periods))
    open_p = close + rng.normal(0, 0.5, periods)
    high = np.maximum(close, open_p) + rng.uniform(0, 1.0, periods)
    low = np.minimum(close, open_p) - rng.uniform(0, 1.0, periods)
    volume = rng.randint(1000, 10000, periods).astype(float)
    return pd.DataFrame(
        {"Open": open_p, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


def verify_no_lookahead(
    func: Callable[[Union[pd.Series, pd.DataFrame], int], Any],
    data: Union[pd.Series, pd.DataFrame],
    t: int
) -> bool:
    """
    Given a function func(data, t) -> signal, perturb data at indices > t
    and assert that the returned signal is unchanged.
    """
    # 1. Run func on original data at index t
    original_signal = func(data, t)
    
    # 2. Perturb data at indices > t
    data_perturbed = data.copy()
    if isinstance(data_perturbed, pd.DataFrame):
        for col in data_perturbed.columns:
            if np.issubdtype(data_perturbed[col].dtype, np.number):
                # Add large noise or set to random values
                data_perturbed.loc[data_perturbed.index[t + 1]:, col] = 99999.9
    else:
        # Series
        data_perturbed.iloc[t + 1:] = 99999.9
        
    # 3. Run func on perturbed data at index t
    perturbed_signal = func(data_perturbed, t)
    
    # 4. Assert equality (accounting for float tolerance or NaN)
    if pd.isna(original_signal) and pd.isna(perturbed_signal):
        return True
    
    if isinstance(original_signal, (float, int, np.number)) and isinstance(perturbed_signal, (float, int, np.number)):
        return bool(np.isclose(original_signal, perturbed_signal, equal_nan=True))
        
    return bool(original_signal == perturbed_signal)

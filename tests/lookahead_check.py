import pandas as pd
import numpy as np
from typing import Callable, Any, Union

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

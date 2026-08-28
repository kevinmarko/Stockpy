"""Covariate/feature drift detection via the Population Stability Index (PSI).

Compares a reference window of a feature's distribution against a recent
window (see ``adapt_symbol_history_to_windows``), buckets both by the
reference's own quantiles, and computes PSI = sum((curr% - ref%) * ln(curr% /
ref%)) per bucket. ``check_and_alert_feature_drift`` is the intended entry
point: it treats an insufficient-data window as an explicit
``PSIResult(psi=None, drift_detected=False, details="Insufficient data")``
rather than a fabricated PSI value, and fires ``send_alert_fn`` whenever PSI
crosses ``PSI_ALERT_THRESHOLD`` (0.25, the standard PSI "moderate shift"
cutoff) or is infinite (a bucket losing all reference mass).
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, Tuple, Sequence, Callable
import logging

logger = logging.getLogger(__name__)

PSI_ALERT_THRESHOLD = 0.25

@dataclass(frozen=True)
class PSIResult:
    drift_detected: bool
    psi: Optional[float]
    feature: str
    details: str

def compute_psi(reference: pd.Series, current: pd.Series, n_buckets: int = 10) -> float:
    """Compute Population Stability Index between reference and current distribution.

    Returns NaN when PSI cannot be computed (empty input, degenerate/single-value
    bucket edges, or an unexpected error during binning) — callers must not
    interpret a NaN as "confirmed no drift". Returns +inf when reference has a
    single value and current introduces variance (treated as maximum drift).
    """
    if len(reference) == 0 or len(current) == 0:
        return float('nan')

    # Handle cases with no variance in reference
    if reference.nunique() <= 1:
        # If both have same single value, no drift. If current has different values, drift.
        if current.nunique() == 1 and current.iloc[0] == reference.iloc[0]:
            return 0.0
        # If variance is introduced, we could say it's maximum drift
        return float('inf')

    try:
        # Define bucket bins based on reference quantiles
        bins = np.unique(np.percentile(reference.dropna(), np.linspace(0, 100, n_buckets + 1)))
        if len(bins) < 2:
            return float('nan')

        # Ensure extremes are caught
        bins[0] = -np.inf
        bins[-1] = np.inf

        ref_percents = pd.cut(reference, bins=bins).value_counts(normalize=True).sort_index()
        curr_percents = pd.cut(current, bins=bins).value_counts(normalize=True).sort_index()

        # Replace 0 with small epsilon to avoid division by zero or log(0)
        epsilon = 1e-4
        ref_percents = ref_percents.replace(0, epsilon)
        curr_percents = curr_percents.replace(0, epsilon)

        psi = np.sum((curr_percents - ref_percents) * np.log(curr_percents / ref_percents))
        return float(psi)
    except Exception as e:
        logger.warning(f"Error computing PSI: {e}")
        return float('nan')

def adapt_symbol_history_to_windows(df: pd.DataFrame, column: str, reference_size: int = 60, recent_size: int = 20) -> Tuple[pd.Series, pd.Series]:
    """Split historical data into reference and current windows."""
    if len(df) < reference_size + recent_size:
        return pd.Series(dtype=float), pd.Series(dtype=float)
        
    history = df[column].dropna()
    if len(history) < reference_size + recent_size:
        return pd.Series(dtype=float), pd.Series(dtype=float)
        
    reference = history.iloc[-(reference_size + recent_size):-recent_size]
    current = history.iloc[-recent_size:]
    return reference, current

def check_and_alert_feature_drift(df: pd.DataFrame, columns: Sequence[str], send_alert_fn: Optional[Callable[[str], None]] = None) -> list[PSIResult]:
    """Calculate PSI across features and dispatch alerts via provided callback if threshold exceeded."""
    results = []
    
    for col in columns:
        if col not in df.columns:
            continue
            
        ref, curr = adapt_symbol_history_to_windows(df, col)
        
        if len(ref) == 0 or len(curr) == 0:
            results.append(PSIResult(
                drift_detected=False,
                psi=None,
                feature=col,
                details="Insufficient data"
            ))
            continue
            
        psi = compute_psi(ref, curr)
        
        if np.isinf(psi) or psi >= PSI_ALERT_THRESHOLD:
            msg = f"Feature drift detected for {col}: PSI = {psi:.4f}" if not np.isinf(psi) else f"Feature drift detected for {col}: infinite PSI"
            results.append(PSIResult(
                drift_detected=True,
                psi=psi,
                feature=col,
                details=msg
            ))
            if send_alert_fn:
                send_alert_fn(msg)
        else:
            results.append(PSIResult(
                drift_detected=False,
                psi=psi,
                feature=col,
                details=f"PSI = {psi:.4f} is within normal range"
            ))
            
    return results

"""Bond-BERT Credit & Yield Spread Forecaster (Phase 5).

Forecasts credit spreads and yield curve dynamics using macro interest rate
and fixed income features. Implements the Model(ABC) interface.
"""

from typing import Optional
import numpy as np
import pandas as pd

from ml.models.base import Model


class BondBertModel(Model):
    """Yield curve & credit spread predictive model."""

    def __init__(self, target_horizon_days: int = 10):
        self.target_horizon_days = target_horizon_days
        self.weights = None
        self.is_fitted = False

    def fit(self, X: pd.DataFrame, y: pd.Series, t1: Optional[pd.Series] = None) -> "BondBertModel":
        """Fit model on interest rate / yield spread features."""
        if len(X) == 0:
            raise ValueError("Cannot fit BondBertModel on empty DataFrame X")
        
        clean_X = X.fillna(0.0).values
        clean_y = y.fillna(0.0).values

        reg = 1e-3 * np.eye(clean_X.shape[1])
        self.weights = np.linalg.pinv(clean_X.T @ clean_X + reg) @ clean_X.T @ clean_y
        self.is_fitted = True
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Generate spread/rate predictions."""
        if not self.is_fitted or self.weights is None:
            return np.zeros(len(X))
        
        clean_X = X.fillna(0.0).values
        return np.asarray(clean_X @ self.weights, dtype=float)

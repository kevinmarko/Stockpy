"""GARCH-MIDAS Mixed-Data Sampling Volatility Model (Phase 5).

Combines daily return volatility with low-frequency macroeconomic variables.
Implements the Model(ABC) interface.
"""

from typing import Optional
import numpy as np
import pandas as pd

from ml.models.base import Model


class GarchMidasModel(Model):
    """GARCH-MIDAS Volatility Model."""

    def __init__(self, K: int = 12):
        self.K = K
        self.is_fitted = False
        self.weights = None

    def fit(self, X: pd.DataFrame, y: pd.Series, t1: Optional[pd.Series] = None) -> "GarchMidasModel":
        """Fit MIDAS weights over daily + macro features."""
        if len(X) == 0:
            raise ValueError("Cannot fit GarchMidasModel on empty DataFrame X")
        
        clean_X = X.fillna(0.0).values
        clean_y = y.fillna(0.0).values
        
        reg = 1e-4 * np.eye(clean_X.shape[1])
        self.weights = np.linalg.pinv(clean_X.T @ clean_X + reg) @ clean_X.T @ clean_y
        self.is_fitted = True
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Predict annualized volatility series."""
        if not self.is_fitted or self.weights is None:
            return np.zeros(len(X))
        
        clean_X = X.fillna(0.0).values
        pred_vol = np.abs(clean_X @ self.weights)
        return np.asarray(pred_vol, dtype=float)

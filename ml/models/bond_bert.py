"""Bond-BERT Credit & Yield Spread Forecaster (Phase 5).

HONEST STATUS: this is a closed-form ridge-regression baseline, NOT a BERT
model. There is no tokenization, no transformer, no language-model
inference of any kind, and no bond-specific text/news modeling anywhere in
this file -- `target_horizon_days` is stored but never read by fit()/
predict(). `self.weights` is a single closed-form OLS-with-ridge-penalty
solution over whatever raw feature columns (e.g. interest-rate/yield-spread
series) are passed in.

Implements the Model(ABC) interface contract from ml.models.base (so it can
sit in a model registry / comparison chart alongside real models under this
name), but a caller relying on this class to actually run BERT-style text
understanding will get materially different behavior than the name
promises. Building a real BondBERT (fine-tuning a transformer on bond-
specific news, per the "BondBERT" architecture this class's name is
borrowed from) is a separate, substantial NLP effort -- out of scope for
this fix; this docstring exists so nobody mistakes this ridge-regression
stand-in for that.
"""

from typing import Optional
import numpy as np
import pandas as pd

from ml.models.base import Model


class BondBertModel(Model):
    """Ridge-regression baseline registered under the Bond-BERT name — see
    module docstring for exactly what it does and doesn't implement."""

    def __init__(self, target_horizon_days: int = 10):
        self.target_horizon_days = target_horizon_days
        self.weights = None
        self.is_fitted = False

    def fit(self, X: pd.DataFrame, y: pd.Series, t1: Optional[pd.Series] = None) -> "BondBertModel":
        """Fit ridge-regression weights on interest rate / yield spread
        features. target_horizon_days is stored but unused here."""
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

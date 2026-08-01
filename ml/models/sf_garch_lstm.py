"""SF-GARCH-LSTM Predictive Model (Phase 5).

HONEST STATUS: this is a closed-form ridge-regression baseline, NOT the
named SF-GARCH-LSTM architecture (a Sentiment-Factor GARCH volatility
component feeding an LSTM sequence model). fit()/predict() never use
`sequence_length`/`hidden_dim` (stored but unread), never construct any
sequence windowing, never fit a GARCH/GJR-GARCH volatility process, and
contain no LSTM/neural-network layer at all -- `self.weights` is a single
closed-form OLS-with-ridge-penalty solution over whatever raw feature
columns are passed in.

Implements the Model(ABC) interface contract from ml.models.base (so it can
sit in a model registry / comparison chart alongside real models under this
name), but a caller relying on this class to actually perform GARCH
volatility modeling or LSTM sequence learning will get materially different
behavior than the name promises. Building the real architecture is a
separate, substantial ML engineering effort (GJR-GARCH fitting, a real
sequence model, FinBERT sentiment factor integration) -- out of scope for
this fix; this docstring exists so nobody mistakes this ridge-regression
stand-in for that.
"""

from typing import Optional
from pathlib import Path
import numpy as np
import pandas as pd

from ml.models.base import Model


class SFGarchLSTMModel(Model):
    """Ridge-regression baseline registered under the SF-GARCH-LSTM name —
    see module docstring for exactly what it does and doesn't implement."""

    def __init__(self, sequence_length: int = 10, hidden_dim: int = 32):
        self.sequence_length = sequence_length
        self.hidden_dim = hidden_dim
        self.weights = None
        self.is_fitted = False

    def fit(self, X: pd.DataFrame, y: pd.Series, t1: Optional[pd.Series] = None) -> "SFGarchLSTMModel":
        """Fit ridge-regression weights on feature matrix X and target y.

        sequence_length is checked only as a minimum-row-count guard (no
        sequence windowing is actually built); hidden_dim is unused.
        """
        if len(X) < self.sequence_length:
            raise ValueError(f"Insufficient history ({len(X)}) for sequence length {self.sequence_length}")

        # Closed-form ridge regression -- the model's ENTIRE fit, not a
        # "fallback" for a missing real implementation (see module docstring).
        clean_X = X.fillna(0.0).values
        clean_y = y.fillna(0.0).values

        reg = 1e-4 * np.eye(clean_X.shape[1])
        self.weights = np.linalg.pinv(clean_X.T @ clean_X + reg) @ clean_X.T @ clean_y
        self.is_fitted = True
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Generate point predictions returning np.ndarray shape (n_samples,)."""
        if not self.is_fitted or self.weights is None:
            # Degrade to zeros if unfitted (CONSTRAINT #4)
            return np.zeros(len(X))
        
        clean_X = X.fillna(0.0).values
        preds = clean_X @ self.weights
        return np.asarray(preds, dtype=float)

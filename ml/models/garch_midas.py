"""GARCH-MIDAS Mixed-Data Sampling Volatility Model (Phase 5).

HONEST STATUS: this is a closed-form ridge-regression baseline over
whatever raw feature columns are passed in, NOT a GARCH-MIDAS model. There
is no GARCH short-run volatility component, no MIDAS lag-weighting
polynomial, and no separation of high-/low-frequency (mixed-data-sampling)
inputs anywhere in this file -- `K` (the MIDAS lag-polynomial length in the
real architecture) is stored but never read by fit()/predict().
`self.weights` is a single closed-form OLS-with-ridge-penalty solution;
predict() just takes the absolute value of the linear combination to keep
the output non-negative (volatility-shaped), which is not equivalent to an
actual GARCH conditional-variance process.

Implements the Model(ABC) interface contract from ml.models.base (so it can
sit in a model registry / comparison chart alongside real models under this
name), but a caller relying on this class to actually run GARCH-MIDAS
volatility forecasting will get materially different behavior than the name
promises. Building the real architecture (a GARCH short-run component, a
MIDAS long-run component with Beta-weighted lag polynomials over low-
frequency macro series) is a separate, substantial econometrics effort --
out of scope for this fix; this docstring exists so nobody mistakes this
ridge-regression stand-in for that.
"""

from typing import Optional
import numpy as np
import pandas as pd

from ml.models.base import Model


class GarchMidasModel(Model):
    """Ridge-regression baseline registered under the GARCH-MIDAS name —
    see module docstring for exactly what it does and doesn't implement."""

    def __init__(self, K: int = 12):
        self.K = K
        self.is_fitted = False
        self.weights = None

    def fit(self, X: pd.DataFrame, y: pd.Series, t1: Optional[pd.Series] = None) -> "GarchMidasModel":
        """Fit ridge-regression weights over the raw feature columns. K
        (the MIDAS lag length in a real GARCH-MIDAS model) is stored but
        unused here -- no lag polynomial is built."""
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


if __name__ == "__main__":
    pass

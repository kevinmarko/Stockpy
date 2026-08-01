"""Retail Social Sentiment & Emoji Model (Phase 5).

HONEST STATUS: this is a stateless deterministic transform (row-wise mean of
whatever numeric columns are present, clipped to [-1, 1]), NOT a trained
sentiment model. There is no emoji parsing, no emoji-to-polarity mapping, no
social-volume weighting, and no learning of any kind -- fit(X, y) ignores
BOTH arguments entirely and only flips `is_fitted = True`; predict()'s
output has no relationship to y and would be identical for any two calls
with the same X regardless of what this was "fit" on.

Implements the Model(ABC) interface contract from ml.models.base (so it can
sit in a model registry / comparison chart alongside real models under this
name), but a caller relying on this class to actually score emoji/social
sentiment will get materially different behavior than the name promises.
Building a real Emoji Sentiment Dictionary lookup + retail-volume weighting
(per the blueprint this class's name is borrowed from) is a separate,
scoped feature -- out of scope for this fix; this docstring exists so
nobody mistakes this mean-of-columns stand-in for that.
"""

from typing import Optional
import numpy as np
import pandas as pd

from ml.models.base import Model


class EmojiSentimentModel(Model):
    """Stateless mean-of-numeric-columns transform registered under the
    Emoji Sentiment name — see module docstring for exactly what it does
    and doesn't implement."""

    def __init__(self):
        self.is_fitted = False

    def fit(self, X: pd.DataFrame, y: pd.Series, t1: Optional[pd.Series] = None) -> "EmojiSentimentModel":
        """No-op: X and y are both ignored. Only marks the instance fitted
        so predict() will run (see module docstring)."""
        self.is_fitted = True
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Predict sentiment score in [-1.0, 1.0]."""
        if not self.is_fitted or X.empty:
            return np.zeros(len(X))
        
        # Calculate mean across numeric sentiment features if present
        numeric_cols = X.select_dtypes(include=[np.number]).columns
        if len(numeric_cols) == 0:
            return np.zeros(len(X))
        
        scores = X[numeric_cols].mean(axis=1).fillna(0.0).values
        return np.clip(scores, -1.0, 1.0)

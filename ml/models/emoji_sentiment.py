"""Retail Social Sentiment & Emoji Model (Phase 5).

Real implementation of the three things the original stand-in's HONEST
STATUS docstring flagged as missing:

1. **Emoji parsing** -- when ``X`` includes a ``social_text`` column (raw
   social-media text, e.g. Reddit posts already fetched by
   ``data/sentiment_sources.py::RedditSource``), each row is scored via
   ``data/emoji_lexicon.py::score_text_emojis`` -- a real, deterministic
   lookup against a hand-curated Emoji Sentiment Lexicon (see that module's
   own honesty note on what "hand-curated" means here), not a fabricated
   number. Rows with no scoreable emoji contribute a neutral 0.0 to this
   channel, never a fabricated guess.
2. **Retail-volume weighting** -- when ``X`` includes a ``social_volume``
   column (post/comment count), it is folded in as a real feature
   (log1p-transformed, standard practice for a skewed count) alongside the
   emoji-sentiment channel. The actual weight given to volume vs. emoji
   sentiment vs. any other numeric columns present is LEARNED by the ridge
   fit below, not a hand-picked formula.
3. **Learning** -- ``fit(X, y)`` now genuinely uses ``y``: closed-form ridge
   regression (same pattern as ``ml.models.sf_garch_lstm``/``garch_midas``)
   over whatever numeric columns ``X`` provides, plus the derived
   emoji/volume channels when available. The old version ignored both
   arguments and returned a stateless mean.

Graceful degradation, preserved from the original: absent ``social_text``/
``social_volume`` (e.g. this model asked to score a purely numeric feature
matrix, as ``tests/test_phase5_models.py``'s generic smoke test does), it
falls back to fitting ridge on whatever numeric columns ARE present --
still real learning, just without the two derived channels. Output stays
clipped to [-1.0, 1.0] to preserve the "sentiment score" contract.

Remaining, explicitly out of scope: no volume-weighted BACKTEST validation
has been run (this repo's sandboxed dev/CI environment has no live-market
network access), and this is not registered in ``ml/registry.yaml`` or
wired into any production caller.
"""

from typing import Optional, List
import numpy as np
import pandas as pd

from ml.models.base import Model
from data.emoji_lexicon import score_text_emojis


class EmojiSentimentModel(Model):
    """Real ridge-regression model over numeric features plus (when
    available) a real emoji-lexicon sentiment channel and a real
    social-volume channel -- see module docstring for exactly what's
    genuine here and what remains unvalidated."""

    def __init__(self):
        self.weights: Optional[np.ndarray] = None
        self.numeric_columns: List[str] = []
        self.use_emoji: bool = False
        self.use_volume: bool = False
        self.is_fitted = False

    def _emoji_channel(self, X: pd.DataFrame) -> np.ndarray:
        scores = X["social_text"].apply(
            lambda t: score_text_emojis(t) if isinstance(t, str) else None
        )
        return pd.to_numeric(scores, errors="coerce").fillna(0.0).to_numpy(dtype=float)

    def _volume_channel(self, X: pd.DataFrame) -> np.ndarray:
        volume = pd.to_numeric(X["social_volume"], errors="coerce").fillna(0.0)
        return np.log1p(volume.clip(lower=0.0)).to_numpy(dtype=float)

    def _build_features(self, X: pd.DataFrame) -> np.ndarray:
        channels = []
        if self.numeric_columns:
            channels.append(X[self.numeric_columns].fillna(0.0).to_numpy(dtype=float))
        if self.use_emoji:
            channels.append(self._emoji_channel(X).reshape(-1, 1))
        if self.use_volume:
            channels.append(self._volume_channel(X).reshape(-1, 1))
        if not channels:
            return np.zeros((len(X), 1))
        return np.hstack(channels)

    def fit(self, X: pd.DataFrame, y: pd.Series, t1: Optional[pd.Series] = None) -> "EmojiSentimentModel":
        """Real ridge fit -- see module docstring. ``numeric_columns``,
        ``use_emoji``, ``use_volume`` are captured at fit time so predict()
        rebuilds an identical feature matrix (same columns, same order)."""
        self.numeric_columns = list(X.select_dtypes(include=[np.number]).columns)
        self.use_emoji = "social_text" in X.columns
        self.use_volume = "social_volume" in X.columns

        features = np.nan_to_num(self._build_features(X), nan=0.0)
        clean_y = y.fillna(0.0).to_numpy(dtype=float)

        reg = 1e-4 * np.eye(features.shape[1])
        self.weights = np.linalg.pinv(features.T @ features + reg) @ features.T @ clean_y
        self.is_fitted = True
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Predict sentiment score in [-1.0, 1.0]."""
        if not self.is_fitted or self.weights is None or X.empty:
            return np.zeros(len(X))

        features = np.nan_to_num(self._build_features(X), nan=0.0)
        preds = features @ self.weights
        return np.clip(preds, -1.0, 1.0)

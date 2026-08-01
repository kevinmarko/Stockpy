"""
tests/test_emoji_sentiment_model.py
=====================================
Tests for ml/models/emoji_sentiment.py's real ridge-regression + emoji
lexicon + social-volume-weighting implementation -- see that module's
docstring for what's genuine vs. what still degrades gracefully.
"""
import numpy as np
import pandas as pd
import pytest

from ml.models.emoji_sentiment import EmojiSentimentModel


def _social_df(n=80, seed=0):
    rng = np.random.default_rng(seed)
    texts = rng.choice(
        ["Loving this rally 🚀🔥", "This is a disaster 😭📉", "no emoji here", "Solid quarter 👍📈"],
        size=n,
    )
    volume = rng.integers(1, 5000, size=n)
    return pd.DataFrame({"social_text": texts, "social_volume": volume})


class TestFitUsesY:
    def test_fit_actually_learns_from_y_numeric_only(self):
        """The original stand-in ignored y entirely -- predictions for two
        different y targets on the same X must now differ."""
        rng = np.random.default_rng(1)
        X = pd.DataFrame({"feature": rng.normal(0, 1, 100)})
        y1 = pd.Series(rng.normal(0, 1, 100))
        y2 = pd.Series(rng.normal(5, 1, 100))

        m1 = EmojiSentimentModel().fit(X, y1)
        m2 = EmojiSentimentModel().fit(X, y2)

        assert not np.allclose(m1.predict(X), m2.predict(X))

    def test_weights_depend_on_y(self):
        X = pd.DataFrame({"feature": np.linspace(-1, 1, 50)})
        y_pos = pd.Series(np.linspace(-1, 1, 50))  # positively correlated
        y_neg = pd.Series(np.linspace(1, -1, 50))  # negatively correlated

        m_pos = EmojiSentimentModel().fit(X, y_pos)
        m_neg = EmojiSentimentModel().fit(X, y_neg)
        assert np.sign(m_pos.weights[0]) != np.sign(m_neg.weights[0])


class TestEmojiAndVolumeChannels:
    def test_uses_emoji_and_volume_columns_when_present(self):
        model = EmojiSentimentModel()
        df = _social_df()
        model.fit(df, pd.Series(np.random.default_rng(2).normal(0, 1, len(df))))
        assert model.use_emoji is True
        assert model.use_volume is True
        preds = model.predict(df)
        assert len(preds) == len(df)
        assert np.all(np.isfinite(preds))
        assert np.all(preds >= -1.0) and np.all(preds <= 1.0)

    def test_falls_back_to_numeric_only_without_social_columns(self):
        model = EmojiSentimentModel()
        df = pd.DataFrame({"feature": np.random.default_rng(3).normal(0, 1, 60)})
        model.fit(df, df["feature"])
        assert model.use_emoji is False
        assert model.use_volume is False
        preds = model.predict(df)
        assert len(preds) == len(df)

    def test_emoji_text_changes_predictions_holding_volume_fixed(self):
        """Swapping in a differently-worded (differently-emoji-scored) text
        column, same volume, must change predictions -- proves the emoji
        channel is actually load-bearing, not a no-op."""
        rng = np.random.default_rng(4)
        volume = rng.integers(1, 1000, size=40)
        y = pd.Series(rng.normal(0, 1, 40))

        df_pos = pd.DataFrame({"social_text": ["Amazing news 🚀🎉"] * 40, "social_volume": volume})
        df_neg = pd.DataFrame({"social_text": ["Terrible crash 😭📉"] * 40, "social_volume": volume})

        model = EmojiSentimentModel().fit(df_pos, y)
        preds_pos = model.predict(df_pos)
        preds_neg = model.predict(df_neg)
        assert not np.allclose(preds_pos, preds_neg)

    def test_no_known_emoji_degrades_to_neutral_channel_not_crash(self):
        model = EmojiSentimentModel()
        df = pd.DataFrame({
            "social_text": ["no emoji at all"] * 20,
            "social_volume": np.random.default_rng(5).integers(1, 100, size=20),
        })
        model.fit(df, pd.Series(np.random.default_rng(6).normal(0, 1, 20)))
        preds = model.predict(df)
        assert np.all(np.isfinite(preds))


class TestPersistenceAndDegradation:
    def test_save_load_predict_roundtrip_exact(self, tmp_path):
        model = EmojiSentimentModel()
        df = _social_df()
        y = pd.Series(np.random.default_rng(7).normal(0, 1, len(df)))
        model.fit(df, y)
        preds = model.predict(df)

        path = tmp_path / "model.pkl"
        model.save(path)
        reloaded = EmojiSentimentModel.load(path)
        np.testing.assert_array_equal(reloaded.predict(df), preds)

    def test_unfitted_predict_returns_zeros(self):
        model = EmojiSentimentModel()
        df = pd.DataFrame({"feature": np.random.default_rng(8).normal(0, 1, 10)})
        preds = model.predict(df)
        assert np.all(preds == 0.0)

    def test_empty_dataframe_predict_returns_empty(self):
        model = EmojiSentimentModel()
        df = pd.DataFrame({"feature": [1.0, 2.0]})
        model.fit(df, df["feature"])
        preds = model.predict(pd.DataFrame({"feature": []}))
        assert len(preds) == 0

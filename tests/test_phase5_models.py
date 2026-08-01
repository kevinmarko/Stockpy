"""
tests/test_phase5_models.py
============================
Smoke + save/load/predict round-trip tests for the four Phase 5 models.

Each round-trip test asserts predict() on the reloaded model reproduces the
pre-save model's predictions exactly -- not just is_fitted, which a model
that pickled without its fitted weights (e.g. a bug that dropped `self.weights`
in __getstate__, or a save() that wrote before fit() populated the weights)
would still pass while returning nonsense predictions after load.
"""
import pytest
import numpy as np
import pandas as pd
import tempfile
import os
from ml.models.sf_garch_lstm import SFGarchLSTMModel
from ml.models.bond_bert import BondBertModel
from ml.models.emoji_sentiment import EmojiSentimentModel
from ml.models.garch_midas import GarchMidasModel

def test_sf_garch_lstm_smoke():
    model = SFGarchLSTMModel()
    df = pd.DataFrame({'returns': np.random.randn(100)})

    model.fit(df, df['returns'])
    preds = model.predict(df)
    assert isinstance(preds, np.ndarray)
    assert len(preds) == len(df)

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "model")
        model.save(path)
        new_model = SFGarchLSTMModel.load(path)
        assert new_model.is_fitted
        reloaded_preds = new_model.predict(df)
        np.testing.assert_array_equal(reloaded_preds, preds)

def test_bond_bert_smoke():
    model = BondBertModel()
    df = pd.DataFrame({'feature': np.random.randn(100)})

    model.fit(df, df['feature'])
    preds = model.predict(df)
    assert isinstance(preds, np.ndarray)
    assert len(preds) == len(df)

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "model")
        model.save(path)
        new_model = BondBertModel.load(path)
        assert new_model.is_fitted
        reloaded_preds = new_model.predict(df)
        np.testing.assert_array_equal(reloaded_preds, preds)

def test_emoji_sentiment_smoke():
    model = EmojiSentimentModel()
    df = pd.DataFrame({'feature': np.random.randn(100)})

    model.fit(df, df['feature'])
    preds = model.predict(df)
    assert isinstance(preds, np.ndarray)
    assert len(preds) == len(df)

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "model")
        model.save(path)
        new_model = EmojiSentimentModel.load(path)
        assert new_model.is_fitted
        reloaded_preds = new_model.predict(df)
        np.testing.assert_array_equal(reloaded_preds, preds)

def test_garch_midas_smoke():
    model = GarchMidasModel()
    df = pd.DataFrame({'returns': np.random.randn(100), 'macro_factor': np.random.randn(100)})

    model.fit(df, df['returns'])
    preds = model.predict(df)
    assert isinstance(preds, np.ndarray)
    assert len(preds) == len(df)

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "model")
        model.save(path)
        new_model = GarchMidasModel.load(path)
        assert new_model.is_fitted
        reloaded_preds = new_model.predict(df)
        np.testing.assert_array_equal(reloaded_preds, preds)

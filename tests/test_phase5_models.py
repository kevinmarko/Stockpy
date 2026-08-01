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

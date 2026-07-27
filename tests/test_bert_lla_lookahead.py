"""No-lookahead test for ForecastingEngine.run_bert_lla_forecast /
forecasting/bert_lla.py.

Two tiers, matching tests/test_bert_lla.py's own honest split (torch is not
installed in this environment):

1. The scaler-fit-on-train / supervised-window-building machinery
   run_bert_lla_forecast calls is `fit_scalers_on_train` /
   `make_direct_multistep_windows` -- the EXACT SAME, UNMODIFIED functions
   `run_cnn_lstm_forecast` already uses, already covered end-to-end by
   tests/test_forecasting_lookahead.py's perturbation tests (train-only
   scaler fit, no leakage into the reserved inference tail). Reusing those
   functions unchanged means that leakage-safety property transfers
   directly to BERT-LLA -- this file does not re-derive it, it documents
   the transfer and adds BERT-LLA-SPECIFIC leakage risks only.
2. The one NEW leakage surface BERT-LLA introduces beyond CNN-LSTM's own --
   aligning the composite sentiment index to the feature window's date
   range (`ForecastingEngine._sentiment_daily_for_symbol`) -- is torch-
   independent and tested directly here, unconditionally.

The full run_bert_lla_forecast() control-flow test (n_reserve derivation,
window construction call args) needs a real torch.nn.Module to subclass
BertLLARegressor/LLAAttention at import time -- unlike the CNN-LSTM path's
Keras Sequential/Conv1D/LSTM (used as runtime factory calls, mockable via a
fake tensorflow module per tests/test_forecasting_lookahead.py's
established trick), torch.nn.Module is subclassed at MODULE IMPORT TIME,
which a MagicMock cannot stand in for without misrepresenting what's
actually being verified. Skipped here, not faked; will run in an
environment with torch installed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from forecasting.bert_lla import TORCH_AVAILABLE
from forecasting_engine import ForecastingEngine

_skip_no_torch = pytest.mark.skipif(
    not TORCH_AVAILABLE, reason="torch not installed in this environment"
)


class TestSentimentDateRangeNeverWidens:
    """The one leakage surface BERT-LLA adds beyond CNN-LSTM: aligning
    signals.sentiment_index to the feature window. The requested
    [start_day, end_day] passed to compute_sentiment_index must exactly
    match the feature frame's own date span -- never widened past it
    (which could pull in a sentiment reading from after the window)."""

    def test_date_range_passed_through_unwidened(self):
        with patch("signals.sentiment_index.compute_sentiment_index") as mock_compute:
            mock_compute.return_value = {}
            ForecastingEngine._sentiment_daily_for_symbol("AAPL", "2026-07-01", "2026-07-21")

        mock_compute.assert_called_once()
        args, _ = mock_compute.call_args
        assert args[0] == ["AAPL"]
        assert args[1] == "2026-07-01"
        assert args[2] == "2026-07-21"

    def test_failure_degrades_to_empty_never_raises(self):
        """CONSTRAINT #6: a sentiment-read failure must never propagate
        into run_bert_lla_forecast's own try/except as an unhandled error
        that skips the zero-sentinel degrade."""
        with patch(
            "signals.sentiment_index.compute_sentiment_index",
            side_effect=RuntimeError("db down"),
        ):
            result = ForecastingEngine._sentiment_daily_for_symbol("AAPL", "2026-07-01", "2026-07-21")
        assert result == {}

    def test_disabled_sentiment_index_yields_empty_not_fabricated(self):
        """SENTIMENT_INDEX_ENABLED=False (today's default) -- compute_
        sentiment_index itself returns {} -- must flow through as an
        honestly empty dict, never synthesized sentiment."""
        with patch("settings.settings.SENTIMENT_INDEX_ENABLED", False):
            result = ForecastingEngine._sentiment_daily_for_symbol("AAPL", "2026-07-01", "2026-07-21")
        assert result == {}


class TestNoSymbolNoLeakage:
    def test_bert_lla_ablation_without_symbol_never_touches_sentiment(self):
        """A missing `symbol` must short-circuit before any sentiment read
        is attempted -- there is nothing to align a date range to."""
        with patch("settings.settings.BERT_LLA_ENABLED", True), \
             patch("forecasting_engine.TORCH_AVAILABLE", True), \
             patch.object(ForecastingEngine, "_sentiment_daily_for_symbol") as mock_sent:
            engine = ForecastingEngine()
            history_df = pd.DataFrame(
                {"Open": [1.0] * 400, "High": [1.0] * 400, "Low": [1.0] * 400,
                 "Close": [1.0] * 400, "Volume": [1.0] * 400},
                index=pd.date_range(end="2026-07-21", periods=400),
            )
            engine.run_bert_lla_forecast(history_df, "bert_lla", symbol=None)
        mock_sent.assert_not_called()


@_skip_no_torch
class TestFullPipelineLookahead:
    """Placeholder for the full run_bert_lla_forecast() perturbation test
    (mirrors tests/test_forecasting_lookahead.py's
    test_forecasting_scaler_fit_on_train_only) -- needs real torch to
    subclass BertLLARegressor/LLAAttention. Not fakeable via a mock module
    the way the Keras path is (see this file's own docstring)."""

    def test_placeholder_run_in_a_torch_installed_environment(self):
        pytest.skip("Full pipeline lookahead coverage requires torch installed.")

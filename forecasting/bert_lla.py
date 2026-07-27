"""
forecasting/bert_lla.py — BERT-LLA Forecaster (PyTorch)
==========================================================
The dual-layer LSTM + self-attention regressor behind three registered
ablations (see ``forecasting.forecast_tracker.ALL_MODEL_NAMES``):

* ``lstm_baseline``  — LSTM(64) -> Dropout(0.1) -> LSTM(32) -> Dropout(0.1)
                        -> last hidden state -> FC(32) -> FC(H). No
                        attention, no sentiment.
* ``lstm_attention`` — same, but pools via the LLA self-attention layer
                        below instead of the last hidden state. No sentiment.
* ``bert_lla``        — ``lstm_attention`` PLUS a masked composite-
                        sentiment-index channel (``signals.sentiment_index``).

Genuine ablations of ONE architecture class (``BertLLARegressor``), not
three separate models — this is what makes the resulting comparison
(``forecasting_engine.py``'s webapp-facing forecast-error chart) an honest
apples-to-apples read rather than three unrelated models compared under one
label.

``TORCH_AVAILABLE`` guard mirrors ``forecasting_engine.py``'s
``TENSORFLOW_AVAILABLE`` pattern — absent the optional ``torch`` package
(already in ``requirements-optional.txt`` for local FinBERT inference), the
caller degrades to the zero sentinel, never a fabricated forecast
(CONSTRAINT #4).

Self-attention math (``LLAAttention``), verbatim from the source
methodology:

    u_t = tanh(W h_t)
    alpha_t = exp(u_t^T u_context) / sum_k exp(u_k^T u_context)
    y_hat = sum_t alpha_t h_t

Deliberately NOT routed through a subprocess pool (contrast with the
CNN-LSTM path's ``cnn_lstm_process_pool.py``): that pool exists for a
specific, evidenced TensorFlow/pyarrow Abseil symbol collision
(``docs/known_issues/cnn_lstm_tf_deadlock.md``) with no torch equivalent on
record. ``fit_predict_bert_lla`` is still a pure numpy-in/numpy-out
function (not a bound method) so routing it through a pool later, if ever
needed, is a one-line change plus a thin worker module — not a refactor.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

if not TORCH_AVAILABLE:
    logger.debug("torch not available. BERT-LLA forecaster will fall back to the zero sentinel.")

ABLATIONS: Tuple[str, ...] = ("lstm_baseline", "lstm_attention", "bert_lla")

_SENTIMENT_FEATURE_COLS = ["S_t_filled", "S_t_observed"]


if TORCH_AVAILABLE:

    class LLAAttention(nn.Module):
        """Self-attention over LSTM outputs: u_t = tanh(W h_t); alpha =
        softmax(u_t^T u_context); context = sum(alpha * h_t)."""

        def __init__(self, hidden_dim: int) -> None:
            super().__init__()
            self.W = nn.Linear(hidden_dim, hidden_dim)
            self.u_context = nn.Parameter(torch.randn(hidden_dim, 1))

        def forward(self, lstm_outputs: "torch.Tensor") -> Tuple["torch.Tensor", "torch.Tensor"]:
            # lstm_outputs: [batch, seq_len, hidden_dim]
            u_t = torch.tanh(self.W(lstm_outputs))
            scores = torch.matmul(u_t, self.u_context)  # [batch, seq_len, 1]
            alpha = torch.softmax(scores, dim=1)
            context = torch.sum(alpha * lstm_outputs, dim=1)  # [batch, hidden_dim]
            return context, alpha

    class BertLLARegressor(nn.Module):
        """Shared architecture behind all three ablations. ``use_attention=
        False`` reproduces ``lstm_baseline`` (pools via the last hidden
        state instead of attention); ``True`` reproduces ``lstm_attention``/
        ``bert_lla`` (the sentiment channel itself is an INPUT difference,
        handled by the caller building a wider ``input_dim``, not by this
        class)."""

        def __init__(
            self,
            input_dim: int,
            *,
            lstm1_units: int = 64,
            lstm2_units: int = 32,
            dropout_rate: float = 0.1,
            use_attention: bool = True,
            output_dim: int = 1,
        ) -> None:
            super().__init__()
            self.use_attention = use_attention
            self.lstm1 = nn.LSTM(input_dim, lstm1_units, num_layers=1, batch_first=True)
            self.dropout1 = nn.Dropout(dropout_rate)
            self.lstm2 = nn.LSTM(lstm1_units, lstm2_units, num_layers=1, batch_first=True)
            self.dropout2 = nn.Dropout(dropout_rate)
            if use_attention:
                self.attention = LLAAttention(lstm2_units)
            self.fc1 = nn.Linear(lstm2_units, lstm2_units)
            self.fc2 = nn.Linear(lstm2_units, output_dim)

        def forward(self, x: "torch.Tensor") -> Tuple["torch.Tensor", Optional["torch.Tensor"]]:
            out1, _ = self.lstm1(x)
            out1 = self.dropout1(out1)
            out2, _ = self.lstm2(out1)
            out2 = self.dropout2(out2)
            if self.use_attention:
                context, alpha = self.attention(out2)
            else:
                context = out2[:, -1, :]  # last hidden state -- no attention pooling
                alpha = None
            fc1_out = torch.relu(self.fc1(context))
            prediction = self.fc2(fc1_out)
            return prediction, alpha

else:
    LLAAttention = None  # type: ignore[assignment]
    BertLLARegressor = None  # type: ignore[assignment]


def build_masked_sentiment_channel(
    dates: List[str], sentiment_by_day: Dict[str, Dict[str, Any]],
) -> Tuple[np.ndarray, np.ndarray]:
    """Build ``(s_filled, s_observed_mask)`` numpy arrays aligned 1:1 to
    ``dates`` (trading-day labels, one per row of the feature frame).

    A torch tensor cannot carry ``NaN`` through training the way the
    platform's other sentiment outputs correctly do (``signals.
    sentiment_index``'s persisted/reported ``S_t`` stays ``NaN`` when
    unobserved) — this masked encoding exists SOLELY to route around that
    tensor limitation, not because a missing reading is somehow really
    zero. ``s_filled`` is 0.0 wherever ``s_observed_mask`` is 0; the model
    (``BertLLARegressor``, when built with the sentiment channel) is
    expected to learn to consult ``s_filled`` only where the mask says to
    (CONSTRAINT #4 — the underlying missingness is never hidden from the
    model, only encoded so a tensor can represent it)."""
    s_filled = np.zeros(len(dates), dtype=np.float32)
    s_mask = np.zeros(len(dates), dtype=np.float32)
    for i, day in enumerate(dates):
        entry = sentiment_by_day.get(day)
        if entry is not None and entry.get("s_t") is not None:
            s_filled[i] = float(entry["s_t"])
            s_mask[i] = 1.0
    return s_filled, s_mask


def sentiment_coverage(s_mask: np.ndarray) -> float:
    """Fraction of rows with an observed (non-masked) sentiment reading.
    ``0.0`` for an empty mask (never a fabricated non-zero coverage claim)."""
    if len(s_mask) == 0:
        return 0.0
    return float(np.mean(s_mask))


def fit_predict_bert_lla(
    X_train: np.ndarray,
    Y_train: np.ndarray,
    last_window: np.ndarray,
    *,
    use_attention: bool,
    epochs: int = 20,
    lr: float = 0.001,
    dropout_rate: float = 0.1,
) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """Pure numpy-in/numpy-out fit + single-window predict.

    Trains ONE model with a ``Y_train.shape[1]``-wide direct multi-horizon
    output head, for ``epochs`` full-batch epochs — no early stopping or
    validation split (a deliberate v1 simplification versus the CNN-LSTM
    path's more elaborate training regimen; see this module's docstring).

    Returns ``(predictions, alpha)`` — ``predictions`` shape
    ``(n_horizons,)`` in the SAME scaled space as ``Y_train`` (the caller
    inverse-transforms via the train-fit ``scaler_y``); ``alpha`` shape
    ``(window_size,)`` or ``None`` when ``use_attention=False``.

    Raises if ``torch`` is unavailable — the caller
    (``ForecastingEngine.run_bert_lla_forecast``) is responsible for the
    ``TORCH_AVAILABLE`` gate and for catching any exception here to degrade
    to the zero sentinel (CONSTRAINT #6), matching how the CNN-LSTM path's
    outer method — not this inner fit call — owns that responsibility.
    """
    if not TORCH_AVAILABLE:
        raise RuntimeError("torch is not available")

    n_horizons = Y_train.shape[1]
    input_dim = X_train.shape[2]
    model = BertLLARegressor(
        input_dim, dropout_rate=dropout_rate, use_attention=use_attention, output_dim=n_horizons,
    )
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    X_t = torch.tensor(X_train, dtype=torch.float32)
    Y_t = torch.tensor(Y_train, dtype=torch.float32)

    model.train()
    for _ in range(epochs):
        optimizer.zero_grad()
        preds, _ = model(X_t)
        loss = criterion(preds, Y_t)
        loss.backward()
        optimizer.step()

    model.eval()
    with torch.no_grad():
        last_t = torch.tensor(last_window, dtype=torch.float32)
        pred, alpha = model(last_t)

    pred_np = pred.detach().cpu().numpy()[0]
    alpha_np = alpha.detach().cpu().numpy()[0, :, 0] if alpha is not None else None
    return pred_np, alpha_np

"""
InvestYo Quant Platform - Gaussian HMM Regime Detector
=========================================================
Reference: Hamilton, J.D. (1989), "A New Approach to the Economic Analysis of
Nonstationary Time Series and the Business Cycle," Econometrica 57(2):357-384.

Provides a statistical "second opinion" to the rules-based regime
classification in macro_engine.py / MacroEconomicDTO.market_regime. The
rules-based classifier remains primary; this module's output
(hmm_risk_on_probability) is used only to downgrade/confirm, never to
independently override (see macro_engine.py's wiring).

FORWARD (FILTERING) PROBABILITIES, NOT VITERBI / SMOOTHING
-------------------------------------------------------------
hmmlearn's Viterbi decoding (the default `model.predict()`) and its smoothed
posterior (`model.predict_proba()` applied to an interior row of a long
sequence) both use the FULL sequence -- including rows after the row being
labeled -- via the backward pass / global path optimization. That is
in-sample and leaks future information into "today's" regime call.

This module instead uses hmmlearn's `predict_proba()` but takes ONLY THE LAST
ROW of whatever sequence is passed in. This is mathematically equivalent to
pure forward filtering: the standard forward-backward identity is
    gamma_t = alpha_t * beta_t / P(O)
and the backward recursion is seeded with beta_T = 1 (a vector of ones) at
the final time step T of any given sequence -- there is no "after" within a
sequence that ends at T. Therefore gamma_T = alpha_T / P(O), i.e. the
smoothed posterior at the LAST row of a sequence is identical to the pure
forward-filtered probability at that row, for ANY sequence length. The
no-lookahead guarantee comes from never returning (or letting a caller index
into) any row other than the last one -- callers MUST slice their feature
frame to end exactly at the date they want a probability for.

REFIT CADENCE (EXPANDING WINDOW)
-----------------------------------
fit() refits the HMM only if more than `retrain_freq_days` have elapsed
since the last actual fit (or if never fit before). Each actual fit uses
ALL rows of whatever DataFrame is passed (an expanding window is the
caller's responsibility -- pass progressively more history on each call).
Between refits, repeated fit() calls with slightly more data are no-ops:
this is what test_hmm_no_lookahead.py exercises to prove that adding one
more day of data does not retroactively change a recent fit's
distributional fingerprint within the same retrain cycle.
"""

from __future__ import annotations

import logging
import math
from typing import Dict, Optional

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM

logger = logging.getLogger(__name__)

FEATURE_COLUMNS = ["spy_return", "realized_vol_20d", "vix_level", "yield_curve_spread"]
DEFAULT_STATE_LABELS_3 = ["bull", "sideways", "bear"]


def build_feature_matrix(
    spy_price_df: pd.DataFrame,
    vix_series: pd.Series,
    yield_curve_series: pd.Series,
) -> pd.DataFrame:
    """Builds the 4-feature matrix consumed by HMMRegimeDetector.

    Parameters
    ----------
    spy_price_df : pd.DataFrame
        Must contain a 'Close' column, indexed by date.
    vix_series : pd.Series
        Daily VIX level, indexed by date (e.g. DataEngine.fetch_macro_history()['VIXCLS']).
    yield_curve_series : pd.Series
        Daily 10Y-2Y yield curve spread, indexed by date.

    Returns
    -------
    pd.DataFrame
        Columns: spy_return, realized_vol_20d, vix_level, yield_curve_spread.
        Rows with any NaN (e.g. the first 20 days, before the realized-vol
        window fills) are dropped -- never fabricated.

    Notes
    -----
    Each row's features are CONTEMPORANEOUS (use data up to and including
    that row's own date), not next-day-predictive -- this differs from
    processing_engine.py's momentum features (which use .shift(1) because
    they predict a LATER bar's return). Here, the regime classifier is
    inferring "what state are we in AS OF today's close", which legitimately
    uses today's own close -- exactly how the existing rules-based
    macro_engine.py already classifies "today's" regime from "today's"
    snapshot. The no-lookahead property this module guarantees is temporal
    (never use data dated after the row being classified), not
    "same-day-exclusive".
    """
    def _normalize_index(obj):
        """Strips time-of-day and timezone so series from different sources
        (yfinance is often tz-aware with intraday timestamps; FRED is naive,
        midnight) align on calendar date rather than silently producing an
        all-NaN outer join."""
        idx = pd.DatetimeIndex(obj.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        obj = obj.copy()
        obj.index = idx.normalize()
        return obj

    close = _normalize_index(spy_price_df)["Close"]
    vix_series = _normalize_index(vix_series)
    yield_curve_series = _normalize_index(yield_curve_series)

    spy_return = close.pct_change()
    realized_vol_20d = spy_return.rolling(window=20).std() * math.sqrt(252)

    features = pd.DataFrame({
        "spy_return": spy_return,
        "realized_vol_20d": realized_vol_20d,
        "vix_level": vix_series,
        "yield_curve_spread": yield_curve_series,
    })
    features = features.dropna(how="any")
    return features


class HMMRegimeDetector:
    """3-state Gaussian HMM regime detector (Hamilton 1989 regime-switching).

    Parameters
    ----------
    n_states : int
        Number of hidden states (default 3: bull / sideways / bear).
    retrain_freq_days : int
        Minimum number of days that must elapse between actual refits.
        fit() calls within this window of the last real fit are no-ops.
    random_state : int
        Seed for hmmlearn's EM initialization, for deterministic tests.
    """

    def __init__(self, n_states: int = 3, retrain_freq_days: int = 7, random_state: int = 42):
        if n_states < 2:
            raise ValueError("n_states must be >= 2")
        self.n_states = n_states
        self.retrain_freq_days = retrain_freq_days
        self.random_state = random_state

        self.model: Optional[GaussianHMM] = None
        self.last_fit_date: Optional[pd.Timestamp] = None
        self.feature_means_: Optional[np.ndarray] = None
        self.feature_stds_: Optional[np.ndarray] = None
        self.state_labels: Dict[int, str] = {}

    def fit(self, features_df: pd.DataFrame) -> None:
        """Fits (or refits, subject to retrain_freq_days gating) on all rows
        of features_df. Callers control the expanding window by passing
        progressively more history on each call.

        Raises
        ------
        ValueError
            If features_df is empty or contains NaNs (never silently
            dropped here -- the caller's build_feature_matrix() is
            responsible for that).
        """
        if features_df is None or features_df.empty:
            raise ValueError("HMMRegimeDetector.fit: features_df is empty.")
        if features_df.isna().any().any():
            raise ValueError("HMMRegimeDetector.fit: features_df contains NaN values.")

        last_date = pd.Timestamp(features_df.index[-1])

        if self.model is not None and self.last_fit_date is not None:
            days_since_last_fit = (last_date - self.last_fit_date).days
            if days_since_last_fit < self.retrain_freq_days:
                logger.debug(
                    "HMMRegimeDetector.fit: skipping refit (%d days since last fit, "
                    "< retrain_freq_days=%d).", days_since_last_fit, self.retrain_freq_days,
                )
                return

        X = features_df.to_numpy(dtype=float)
        self.feature_means_ = X.mean(axis=0)
        self.feature_stds_ = X.std(axis=0)
        self.feature_stds_[self.feature_stds_ == 0.0] = 1.0
        X_scaled = (X - self.feature_means_) / self.feature_stds_

        model = GaussianHMM(
            n_components=self.n_states,
            covariance_type="diag",
            n_iter=100,
            random_state=self.random_state,
        )
        model.fit(X_scaled)

        self.model = model
        self.last_fit_date = last_date
        self.identify_states_by_vol()
        logger.info(
            "HMMRegimeDetector.fit: refit on %d rows through %s. State labels: %s",
            len(features_df), last_date.date(), self.state_labels,
        )

    def identify_states_by_vol(self) -> Dict[int, str]:
        """Post-fit: sorts hidden states by total fitted (diagonal) variance,
        ascending, and labels them semantically.

        For n_states == 3: ["bull", "sideways", "bear"] (lowest variance ->
        "bull", highest -> "bear"). For other n_states, states beyond the
        available labels are named "state_<index>".

        Returns
        -------
        dict[int, str]
            Maps hidden-state index (as used internally by hmmlearn) to its
            semantic label.
        """
        if self.model is None:
            raise RuntimeError("HMMRegimeDetector.identify_states_by_vol: model not fit yet.")

        # covars_ shape for covariance_type='diag' is (n_states, n_features)
        variances = np.asarray(self.model.covars_).reshape(self.n_states, -1).sum(axis=1)
        order = np.argsort(variances)  # ascending: lowest variance first

        if self.n_states == 3:
            labels = DEFAULT_STATE_LABELS_3
        else:
            labels = [DEFAULT_STATE_LABELS_3[i] if i < len(DEFAULT_STATE_LABELS_3) else f"state_{i}"
                      for i in range(self.n_states)]

        state_labels: Dict[int, str] = {}
        for rank, state_idx in enumerate(order):
            state_labels[int(state_idx)] = labels[rank]
        self.state_labels = state_labels
        return state_labels

    def predict_proba(self, features_df: pd.DataFrame) -> Dict[str, float]:
        """Returns FORWARD (filtered) state probabilities at the LAST ROW of
        features_df only -- see module docstring for why hmmlearn's
        predict_proba()[-1] equals pure forward filtering.

        Parameters
        ----------
        features_df : pd.DataFrame
            Caller MUST slice this to end exactly at the date a probability
            is wanted for. Rows after that date must never be included.

        Returns
        -------
        dict
            {p_state_0, ..., p_state_{n-1}, dominant_state, risk_on_probability}.
            risk_on_probability is the probability mass on the state(s)
            labeled "bull" (the lowest-variance state).
        """
        if self.model is None:
            raise RuntimeError("HMMRegimeDetector.predict_proba: model not fit yet. Call fit() first.")
        if features_df is None or features_df.empty:
            raise ValueError("HMMRegimeDetector.predict_proba: features_df is empty.")
        if features_df.isna().any().any():
            raise ValueError("HMMRegimeDetector.predict_proba: features_df contains NaN values.")

        X = features_df.to_numpy(dtype=float)
        X_scaled = (X - self.feature_means_) / self.feature_stds_

        posteriors = self.model.predict_proba(X_scaled)  # shape (n_rows, n_states)
        last_probs = posteriors[-1]  # forward-filtered prob at the final row (see docstring)

        result: Dict[str, float] = {f"p_state_{i}": float(last_probs[i]) for i in range(self.n_states)}
        result["dominant_state"] = int(np.argmax(last_probs))

        if not self.state_labels:
            self.identify_states_by_vol()
        risk_on_prob = sum(
            float(last_probs[state_idx])
            for state_idx, label in self.state_labels.items()
            if label == "bull"
        )
        result["risk_on_probability"] = float(risk_on_prob)
        return result

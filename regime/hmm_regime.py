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
from typing import Any, Dict, List, Optional

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
    credit_spread_series: Optional[pd.Series] = None,
    inflation_expectation_series: Optional[pd.Series] = None,
    include_vol_term_spread: bool = False,
    standardize_features: bool = False,
    feature_columns: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Builds the feature matrix consumed by HMMRegimeDetector.

    Parameters
    ----------
    spy_price_df : pd.DataFrame
        Must contain a 'Close' column, indexed by date.
    vix_series : pd.Series
        Daily VIX level, indexed by date (e.g. DataEngine.fetch_macro_history()['VIXCLS']).
    yield_curve_series : pd.Series
        Daily 10Y-2Y yield curve spread, indexed by date.
    credit_spread_series : Optional[pd.Series]
        Optional daily High-Yield OAS credit spread (BAMLH0A0HYM2).
    include_vol_term_spread : bool
        If True, computes and includes the 20D minus 60D realized volatility spread.
    feature_columns : Optional[List[str]]
        Optional explicit subset of columns to retain in the returned DataFrame.

    Returns
    -------
    pd.DataFrame
        Columns: spy_return, realized_vol_20d, vix_level, yield_curve_spread,
        and optionally credit_spread, vol_term_spread.
        Rows with any NaN are dropped -- never fabricated.

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

    data_dict: Dict[str, Any] = {
        "spy_return": spy_return,
        "realized_vol_20d": realized_vol_20d,
        "vix_level": vix_series,
        "yield_curve_spread": yield_curve_series,
    }

    if credit_spread_series is not None:
        data_dict["credit_spread"] = _normalize_index(credit_spread_series)

    if inflation_expectation_series is not None:
        data_dict["inflation_expectation"] = _normalize_index(inflation_expectation_series)

    if include_vol_term_spread:
        realized_vol_60d = spy_return.rolling(window=60).std() * math.sqrt(252)
        data_dict["vol_term_spread"] = realized_vol_20d - realized_vol_60d

    features = pd.DataFrame(data_dict)

    if feature_columns is not None:
        valid_cols = [c for c in feature_columns if c in features.columns]
        if valid_cols:
            features = features[valid_cols]

    if standardize_features:
        roll_mean = features.rolling(window=252, min_periods=20).mean()
        roll_std = features.rolling(window=252, min_periods=20).std()
        features = (features - roll_mean) / roll_std

    features = features.dropna(how="any")
    return features


class HMMRegimeDetector:
    """Gaussian HMM regime detector (Hamilton 1989 regime-switching).

    Parameters
    ----------
    n_states : int
        Number of hidden states (default 3: bull / sideways / bear).
    retrain_freq_days : int
        Minimum number of days that must elapse between actual refits.
        fit() calls within this window of the last real fit are no-ops.
    random_state : int
        Seed for hmmlearn's EM initialization, for deterministic tests.
    covariance_type : str
        Covariance structure: 'diag' (default), 'full', 'spherical', 'tied'.
    n_iter : int
        Maximum EM iterations for Gaussian HMM fitting (default 150).
    tol : float
        Convergence threshold for Gaussian HMM EM fitting (default 1e-4).
    """

    def __init__(
        self,
        n_states: int = 3,
        retrain_freq_days: int = 7,
        random_state: int = 42,
        covariance_type: str = "diag",
        n_iter: int = 150,
        tol: float = 1e-4,
        n_inits: int = 1,
        min_covar: float = 1e-3,
    ):
        if n_states < 2:
            raise ValueError("n_states must be >= 2")
        self.n_states = n_states
        self.retrain_freq_days = retrain_freq_days
        self.random_state = random_state
        self.covariance_type = str(covariance_type or "diag").lower().strip()
        if self.covariance_type not in {"diag", "full", "spherical", "tied"}:
            self.covariance_type = "diag"
        self.n_iter = max(10, int(n_iter))
        self.tol = max(1e-8, float(tol))
        self.n_inits = max(1, int(n_inits))
        self.min_covar = float(min_covar)

        self.model: Optional[GaussianHMM] = None
        self.last_fit_date: Optional[pd.Timestamp] = None
        self.feature_means_: Optional[np.ndarray] = None
        self.feature_stds_: Optional[np.ndarray] = None
        self.feature_names_: Optional[List[str]] = None
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

        self.feature_names_ = list(features_df.columns)
        X = features_df.to_numpy(dtype=float)
        self.feature_means_ = X.mean(axis=0)
        self.feature_stds_ = X.std(axis=0)
        # Degenerate-std guard (repo convention, docs/CLAUDE.md): a near-constant
        # (not necessarily bit-identical) feature column produces a std that is
        # near-zero but not exactly 0.0 due to floating-point noise; an exact
        # `== 0.0` check lets that near-zero value through and explodes the
        # scaled feature. Guard with `< 1e-12`, matching every other ratio-over-
        # std computation in this codebase.
        self.feature_stds_[self.feature_stds_ < 1e-12] = 1.0
        X_scaled = (X - self.feature_means_) / self.feature_stds_

        best_score = -float("inf")
        best_model = None

        for init_idx in range(self.n_inits):
            seed = self.random_state + init_idx if self.random_state is not None else None
            model = GaussianHMM(
                n_components=self.n_states,
                covariance_type=self.covariance_type,
                n_iter=self.n_iter,
                tol=self.tol,
                random_state=seed,
                min_covar=self.min_covar,
            )
            if self.model is not None and init_idx == 0:
                # Warm start the EM algorithm using the previous fit
                model.init_params = ""
                model.startprob_ = self.model.startprob_.copy()
                model.transmat_ = self.model.transmat_.copy()
                model.means_ = self.model.means_.copy()
                # hmmlearn's covars_ getter expands to full matrices, but the setter 
                # expects the compact shape. Use _covars_ directly to bypass.
                model.covars_ = self.model._covars_.copy()
                
            try:
                model.fit(X_scaled)
                score = model.score(X_scaled)
                if score > best_score:
                    best_score = score
                    best_model = model
            except Exception as e:
                logger.debug("HMM fit failed for seed %s: %s", seed, e)

        if best_model is None:
            # Fallback if all fail
            model = GaussianHMM(
                n_components=self.n_states,
                covariance_type=self.covariance_type,
                n_iter=self.n_iter,
                tol=self.tol,
                random_state=self.random_state,
                min_covar=self.min_covar,
            )
            model.fit(X_scaled)
        else:
            model = best_model

        # Repair zero-sum rows in transmat_ to guarantee valid Markov transitions
        # and prevent downstream hmmlearn _check_sum_1 failures on edge datasets.
        if hasattr(model, "transmat_"):
            model.transmat_ = np.maximum(model.transmat_, 0.0)
            row_sums = model.transmat_.sum(axis=1)
            zero_rows = row_sums < 1e-12
            if np.any(zero_rows):
                model.transmat_[zero_rows] = 1.0 / self.n_states
            # Re-normalize rows to ensure exact sum to 1.0
            model.transmat_ = model.transmat_ / model.transmat_.sum(axis=1, keepdims=True)

        if hasattr(model, "startprob_"):
            model.startprob_ = np.maximum(model.startprob_, 0.0)
            sp_sum = model.startprob_.sum()
            if sp_sum < 1e-12:
                model.startprob_ = np.ones(self.n_states) / self.n_states
            else:
                model.startprob_ = model.startprob_ / sp_sum

        self.model = model
        self.last_fit_date = last_date
        self.identify_states_by_vol()
        logger.info(
            "HMMRegimeDetector.fit: refit on %d rows through %s (cov=%s, iter=%d). State labels: %s",
            len(features_df), last_date.date(), self.covariance_type, self.n_iter, self.state_labels,
        )

    def _tied_covariance_risk_proxy(self) -> "tuple[np.ndarray, bool]":
        """Ranking proxy for `covariance_type == 'tied'`.

        Tied covariance is identical across all states by construction, so
        there is no per-state variance to extract. This ranks states by the
        fitted mean of a directional risk feature instead (higher mean =
        riskier), which is what identify_states_by_vol()'s ascending sort
        already assumes.

        Priority order: realized_vol_20d -> vix_level -> credit_spread
        (all "higher = riskier" by construction) -> spy_return, negated
        ("higher return = safer", so the sign is flipped so higher-proxy
        still means riskier). yield_curve_spread is deliberately excluded:
        unlike the others its risk direction is ambiguous on the raw level
        alone (an inverted curve signals risk; the level by itself does
        not), so it would not be a safe drop-in for this ascending-sort
        contract.

        Because fit() z-scores every column via (X - mean) / std (a
        monotonic, sign-preserving per-column map; std > 0 is enforced by
        the degenerate-std guard in fit()) before fitting the HMM, "higher
        scaled-mean" implies "higher raw-mean" regardless of that rescale.
        If the caller additionally used
        build_feature_matrix(standardize_features=True), the proxy becomes
        relative to trailing history rather than an absolute level -- a
        documented nuance, not a correctness problem for the ascending sort.

        Returns
        -------
        tuple[np.ndarray, bool]
            (per-state proxy values, is_directional). is_directional is
            True when a genuine directional feature was found (the proxy is
            signed and must NOT be floored by min_covar -- see caller);
            False only for the undirected norm-of-means fallback below
            (which is non-negative and safe to floor like every other
            branch).
        """
        means = np.asarray(self.model.means_, dtype=float)
        feature_names = self.feature_names_ or []
        for risk_feature in ("realized_vol_20d", "vix_level", "credit_spread"):
            if risk_feature in feature_names:
                idx = feature_names.index(risk_feature)
                return means[:, idx], True
        if "spy_return" in feature_names:
            idx = feature_names.index("spy_return")
            return -means[:, idx], True
        logger.error(
            "HMMRegimeDetector._tied_covariance_risk_proxy: none of "
            "realized_vol_20d/vix_level/credit_spread/spy_return present in "
            "feature_names_=%s; falling back to undirected mean-vector norm, "
            "which is NOT guaranteed monotonic with risk and may mislabel "
            "states.", feature_names,
        )
        return np.linalg.norm(means, axis=1), False

    def identify_states_by_vol(self) -> Dict[int, str]:
        """Post-fit: sorts hidden states by total fitted variance,
        ascending, and labels them semantically.

        For n_states == 3: ["bull", "sideways", "bear"] (lowest variance ->
        "bull", highest -> "bear"). For n_states == 2: ["bull", "sideways"]
        (matches n_states == 3's naming for the lower state; there is no
        third bucket to be "bear"). For n_states >= 4: lowest-variance state
        is "bull", highest-variance state is "bear", and any states between
        them get generic "state_<rank>" labels (there is no canonical name
        for a 4th+ regime bucket).

        Returns
        -------
        dict[int, str]
            Maps hidden-state index (as used internally by hmmlearn) to its
            semantic label.
        """
        if self.model is None:
            raise RuntimeError("HMMRegimeDetector.identify_states_by_vol: model not fit yet.")

        # Variance calculation depending on covariance structure:
        is_directional = False
        if self.covariance_type == "full":
            # Trace of covariance matrix per state. hmmlearn's public
            # covars_ getter for 'full' already returns the natural
            # (n_states, n_features, n_features) shape (no compact-form
            # expansion happens for 'full', unlike diag/spherical/tied), so
            # this is safe to read directly.
            variances = np.array([float(np.trace(c)) for c in self.model.covars_])
        elif self.covariance_type == "spherical":
            # hmmlearn's public covars_ getter for 'spherical' does NOT
            # reliably return an (n_states, ...) shaped array (empirically
            # verified against hmmlearn==0.3.3: for n_components=3,
            # n_features=4 it returns shape (12, 4, 4), whose flattened
            # length never equals n_states -- silently tripping the
            # len(variances) != self.n_states fallback below on every
            # single spherical fit). Use the compact internal _covars_
            # array instead, matching this file's own warm-start precedent
            # in fit() ("hmmlearn's covars_ getter expands to full
            # matrices, but the setter expects the compact shape. Use
            # _covars_ directly to bypass."). Verified shape: (n_states,
            # n_features), each row a single scalar variance broadcast
            # across all features -- summing is a constant, order-
            # preserving multiple of the true per-state variance.
            variances = np.asarray(self.model._covars_, dtype=float).reshape(self.n_states, -1).sum(axis=1)
        elif self.covariance_type == "tied":
            variances, is_directional = self._tied_covariance_risk_proxy()
        else:  # diag: covars_ shape is (n_states, n_features)
            variances = np.asarray(self.model.covars_, dtype=float).reshape(self.n_states, -1).sum(axis=1)

        # Enforce min_covar regularization / conditioning on extracted
        # variances -- but ONLY for genuine non-negative variance-like
        # quantities. The tied-covariance directional proxy is a signed,
        # near-zero-centered z-scored mean; flooring it to >= min_covar
        # would collapse every below-floor state (roughly half, in a
        # z-scored feature) to an identical value and silently reintroduce
        # index-order-dependent ties -- the exact failure mode this
        # function exists to eliminate.
        if not is_directional:
            variances = np.maximum(variances, self.min_covar)

        if len(variances) != self.n_states:
            logger.error(
                "HMMRegimeDetector.identify_states_by_vol: extracted variance "
                "array length %d != n_states %d (covariance_type=%s); falling "
                "back to arbitrary index-based ordering, which is NOT based on "
                "volatility and will likely mislabel states.",
                len(variances), self.n_states, self.covariance_type,
            )
            variances = np.arange(self.n_states, dtype=float)

        order = np.argsort(variances)  # ascending: lowest variance first

        if self.n_states == 3:
            labels = DEFAULT_STATE_LABELS_3
        elif self.n_states == 2:
            labels = DEFAULT_STATE_LABELS_3[:2]  # ["bull", "sideways"]
        else:  # n_states >= 4: lowest -> "bull", highest -> "bear", rest generic
            labels = (
                ["bull"]
                + [f"state_{i}" for i in range(1, self.n_states - 1)]
                + ["bear"]
            )

        state_labels: Dict[int, str] = {}
        for rank, state_idx in enumerate(order):
            state_labels[int(state_idx)] = labels[rank]
        self.state_labels = state_labels
        return state_labels

    def predict_proba(self, features_df: pd.DataFrame) -> Dict[str, Any]:
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
            {p_state_0, ..., p_state_{n-1}, dominant_state, risk_on_probability, regime_state_label}.
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

        result: Dict[str, Any] = {f"p_state_{i}": float(last_probs[i]) for i in range(self.n_states)}
        result["dominant_state"] = int(np.argmax(last_probs))

        if not self.state_labels:
            self.identify_states_by_vol()
        risk_on_prob = sum(
            float(last_probs[state_idx])
            for state_idx, label in self.state_labels.items()
            if label == "bull"
        )
        result["risk_on_probability"] = float(risk_on_prob)
        result["regime_state_label"] = self.state_labels[result["dominant_state"]]
        return result

    def compute_diagnostics(self, features_df: pd.DataFrame) -> Dict[str, Any]:
        """Computes comprehensive regime model diagnostics including Log-Likelihood,
        AIC, BIC, transition matrix, expected durations, and empirical regime metrics.

        Parameters
        ----------
        features_df : pd.DataFrame
            Feature dataset with datetime index and features columns.

        Returns
        -------
        Dict[str, Any]
            Diagnostic summary dictionary.
        """
        if self.model is None:
            raise RuntimeError("HMMRegimeDetector.compute_diagnostics: model not fit yet.")
        if features_df is None or features_df.empty:
            raise ValueError("HMMRegimeDetector.compute_diagnostics: features_df is empty.")

        X = features_df.to_numpy(dtype=float)
        X_scaled = (X - self.feature_means_) / self.feature_stds_

        n_samples, n_features = X_scaled.shape
        log_likelihood = float(self.model.score(X_scaled))

        # Parameter count calculation:
        # Initial probas: n_states - 1
        # Transition matrix: n_states * (n_states - 1)
        # Means: n_states * n_features
        # Covariances:
        if self.covariance_type == "diag":
            cov_params = self.n_states * n_features
        elif self.covariance_type == "full":
            cov_params = self.n_states * (n_features * (n_features + 1) // 2)
        elif self.covariance_type == "spherical":
            cov_params = self.n_states
        elif self.covariance_type == "tied":
            cov_params = n_features * (n_features + 1) // 2
        else:
            cov_params = self.n_states * n_features

        num_params = (
            (self.n_states - 1)
            + self.n_states * (self.n_states - 1)
            + self.n_states * n_features
            + cov_params
        )

        if not math.isfinite(log_likelihood):
            log_likelihood = float("-inf")
            aic = float("inf")
            bic = float("inf")
        else:
            aic = float(2 * num_params - 2 * log_likelihood)
            bic = float(num_params * np.log(max(1, n_samples)) - 2 * log_likelihood)

        trans_mat = np.asarray(self.model.transmat_, dtype=float)
        diag_trans = np.diag(trans_mat)
        expected_durations = {}
        for s_idx, p_self in enumerate(diag_trans):
            lbl = self.state_labels.get(s_idx, f"state_{s_idx}")
            # Guard against p_self >= 1.0
            dur = 1.0 / (1.0 - p_self) if (1.0 - p_self) > 1e-12 else float("inf")
            expected_durations[lbl] = float(dur)

        # Compute empirical return and vol by state if spy_return is in features
        state_metrics: Dict[str, Dict[str, float]] = {}
        posteriors = self.model.predict_proba(X_scaled)
        dominant_seq = np.argmax(posteriors, axis=1)

        has_return = "spy_return" in features_df.columns
        spy_ret = features_df["spy_return"].to_numpy(dtype=float) if has_return else None

        for s_idx in range(self.n_states):
            lbl = self.state_labels.get(s_idx, f"state_{s_idx}")
            mask = dominant_seq == s_idx
            count = int(np.sum(mask))
            freq = float(count / max(1, n_samples))

            m_dict: Dict[str, float] = {
                "state_index": s_idx,
                "count": count,
                "frequency": freq,
                "expected_duration_days": expected_durations.get(lbl, 0.0),
            }

            if has_return and spy_ret is not None and count > 1:
                ret_subset = spy_ret[mask]
                mean_ret = float(np.mean(ret_subset) * 252)
                vol_ret = float(np.std(ret_subset) * math.sqrt(252))
                sharpe = float(mean_ret / vol_ret) if vol_ret > 1e-12 else 0.0
                m_dict["ann_return"] = mean_ret
                m_dict["ann_volatility"] = vol_ret
                m_dict["sharpe_ratio"] = sharpe if math.isfinite(sharpe) else 0.0

            state_metrics[lbl] = m_dict

        return {
            "n_states": self.n_states,
            "covariance_type": self.covariance_type,
            "n_features": n_features,
            "n_samples": n_samples,
            "n_parameters": num_params,
            "log_likelihood": log_likelihood,
            "aic": aic,
            "bic": bic,
            "transition_matrix": trans_mat.tolist(),
            "state_labels": self.state_labels,
            "expected_durations_days": expected_durations,
            "state_metrics": state_metrics,
        }


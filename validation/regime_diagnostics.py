"""
InvestYo Quant Platform - Regime Diagnostics & Validation Engine
================================================================
Empirical evaluation, walk-forward validation, and model selection
diagnostics for Gaussian HMM regime detection models.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from regime.hmm_regime import HMMRegimeDetector

logger = logging.getLogger(__name__)


def run_walk_forward_evaluation(
    features_df: pd.DataFrame,
    n_states: int = 3,
    covariance_type: str = "diag",
    retrain_freq_days: int = 7,
    n_iter: int = 150,
    tol: float = 1e-4,
    min_fit_rows: int = 100,
    random_state: int = 42,
) -> pd.DataFrame:
    """Performs strict expanding-window, causal walk-forward prediction across features_df.

    Parameters
    ----------
    features_df : pd.DataFrame
        Contemporaneous feature matrix (must include 'spy_return' and datetime index).
    n_states : int
        Number of hidden states.
    covariance_type : str
        Covariance type ('diag', 'full', 'spherical', 'tied').
    retrain_freq_days : int
        Minimum days between refits.
    n_iter : int
        Max EM iterations.
    tol : float
        EM convergence tolerance.
    min_fit_rows : int
        Initial warm-up period.
    random_state : int
        Seed for reproducibility.

    Returns
    -------
    pd.DataFrame
        DataFrame of walk-forward results with columns:
        [spy_return, dominant_state, dominant_label, risk_on_probability,
         p_state_0, ..., p_state_{n-1}].
    """
    if features_df is None or len(features_df) <= min_fit_rows:
        raise ValueError(
            f"features_df must have more than min_fit_rows ({min_fit_rows}) rows."
        )

    detector = HMMRegimeDetector(
        n_states=n_states,
        retrain_freq_days=retrain_freq_days,
        random_state=random_state,
        covariance_type=covariance_type,
        n_iter=n_iter,
        tol=tol,
    )

    records: List[Dict[str, Any]] = []

    for t in range(min_fit_rows, len(features_df)):
        window = features_df.iloc[: t + 1]
        detector.fit(window)
        proba_res = detector.predict_proba(window)

        dt = window.index[-1]
        dom_state = proba_res["dominant_state"]
        dom_label = detector.state_labels.get(dom_state, f"state_{dom_state}")

        row_dict: Dict[str, Any] = {
            "date": dt,
            "dominant_state": dom_state,
            "dominant_label": dom_label,
            "risk_on_probability": proba_res["risk_on_probability"],
        }

        for col in features_df.columns:
            row_dict[col] = float(window[col].iloc[-1])

        for s_idx in range(n_states):
            row_dict[f"p_state_{s_idx}"] = proba_res.get(f"p_state_{s_idx}", 0.0)

        records.append(row_dict)

    result_df = pd.DataFrame(records)
    if "date" in result_df.columns:
        result_df.set_index("date", inplace=True)
    return result_df


def evaluate_state_performance(
    walk_forward_df: pd.DataFrame,
    return_column: str = "spy_return",
) -> Dict[str, Any]:
    """Computes empirical return, volatility, Sharpe, Sortino, and drawdown
    metrics grouped by detected regime state.

    Parameters
    ----------
    walk_forward_df : pd.DataFrame
        Output of run_walk_forward_evaluation.
    return_column : str
        Name of return column to evaluate.

    Returns
    -------
    Dict[str, Any]
        Dictionary with per-state performance metrics and monotonicity checks.
    """
    if walk_forward_df is None or walk_forward_df.empty:
        raise ValueError("walk_forward_df cannot be empty.")
    if return_column not in walk_forward_df.columns:
        raise ValueError(f"Return column '{return_column}' not found in DataFrame.")

    states = list(walk_forward_df["dominant_label"].unique())
    state_metrics: Dict[str, Dict[str, Any]] = {}
    total_days = len(walk_forward_df)

    vols_for_mono: Dict[str, float] = {}

    for state_label in states:
        sub = walk_forward_df[walk_forward_df["dominant_label"] == state_label]
        rets = sub[return_column].dropna().to_numpy(dtype=float)
        count = len(rets)

        if count == 0:
            continue

        freq = float(count / max(1, total_days))
        mean_ret = float(np.mean(rets)) * 252
        vol = float(np.std(rets)) * math.sqrt(252)
        vols_for_mono[state_label] = vol

        sharpe = float(mean_ret / vol) if vol > 1e-12 else 0.0
        sharpe = sharpe if math.isfinite(sharpe) else 0.0

        # Downside deviation for Sortino
        downside = rets[rets < 0.0]
        if len(downside) > 0:
            downside_std = float(np.sqrt(np.mean(downside ** 2))) * math.sqrt(252)
            sortino = float(mean_ret / downside_std) if downside_std > 1e-12 else 0.0
        else:
            sortino = 0.0
        sortino = sortino if math.isfinite(sortino) else 0.0

        win_rate = float(np.sum(rets > 0.0) / count) if count > 0 else 0.0

        # Cumulative max drawdown within state slices
        cum_ret = np.cumprod(1.0 + rets)
        running_max = np.maximum.accumulate(cum_ret)
        running_max_safe = np.maximum(running_max, 1e-12)
        drawdowns = (cum_ret - running_max_safe) / running_max_safe
        max_dd = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0

        state_metrics[state_label] = {
            "days_count": count,
            "frequency": freq,
            "annualized_return": mean_ret,
            "annualized_volatility": vol,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "win_rate": win_rate,
            "max_drawdown": max_dd,
        }

    # Check volatility monotonicity if standard 3 states exist
    monotonic_vol = None
    if "bull" in vols_for_mono and "sideways" in vols_for_mono and "bear" in vols_for_mono:
        monotonic_vol = bool(
            vols_for_mono["bull"] <= vols_for_mono["sideways"] <= vols_for_mono["bear"]
        )

    return {
        "total_days": total_days,
        "state_metrics": state_metrics,
        "volatility_monotonicity": monotonic_vol,
    }


def compare_model_configurations(
    features_df: pd.DataFrame,
    state_counts: Optional[List[int]] = None,
    covariance_types: Optional[List[str]] = None,
    random_state: int = 42,
) -> List[Dict[str, Any]]:
    """Compares model goodness-of-fit and parameter efficiency across states and covariance types.

    Parameters
    ----------
    features_df : pd.DataFrame
        Contemporaneous feature matrix.
    state_counts : Optional[List[int]]
        List of state counts to test (default: [2, 3, 4]).
    covariance_types : Optional[List[str]]
        List of covariance types to test (default: ['diag', 'full']).
    random_state : int
        Seed for reproducibility.

    Returns
    -------
    List[Dict[str, Any]]
        List of model evaluation results sorted by AIC ascending.
    """
    if state_counts is None:
        state_counts = [2, 3, 4]
    if covariance_types is None:
        covariance_types = ["diag", "full"]

    results: List[Dict[str, Any]] = []

    for n_s in state_counts:
        for cov in covariance_types:
            try:
                detector = HMMRegimeDetector(
                    n_states=n_s,
                    covariance_type=cov,
                    retrain_freq_days=1,
                    random_state=random_state,
                )
                detector.fit(features_df)
                diag = detector.compute_diagnostics(features_df)
                results.append({
                    "n_states": n_s,
                    "covariance_type": cov,
                    "n_parameters": diag["n_parameters"],
                    "log_likelihood": diag["log_likelihood"],
                    "aic": diag["aic"],
                    "bic": diag["bic"],
                    "expected_durations_days": diag["expected_durations_days"],
                })
            except Exception as e:
                logger.warning(
                    f"Model comparison failed for states={n_s}, cov={cov}: {e}"
                )

    results.sort(key=lambda x: x.get("aic", float("inf")))
    return results


def select_optimal_model(
    features_df: pd.DataFrame,
    max_states: int = 4,
    criterion: str = "bic",
    covariance_types: Tuple[str, ...] = ("diag", "full"),
) -> Dict[str, Any]:
    """Evaluates AIC/BIC across candidate state counts and returns the best model configuration.

    Parameters
    ----------
    features_df : pd.DataFrame
        Contemporaneous feature matrix.
    max_states : int
        Maximum number of hidden states to evaluate (checks 2 to max_states).
    criterion : str
        Selection criterion ('aic' or 'bic').
    covariance_types : Tuple[str, ...]
        Tuple of covariance types to test.

    Returns
    -------
    Dict[str, Any]
        Best model configuration details.
    """
    state_counts = list(range(2, max_states + 1))
    results = compare_model_configurations(
        features_df=features_df,
        state_counts=state_counts,
        covariance_types=list(covariance_types),
    )
    
    if not results:
        raise ValueError("Model comparison returned no results.")
        
    crit = criterion.lower()
    if crit not in ["aic", "bic"]:
        raise ValueError("Criterion must be 'aic' or 'bic'")
        
    results.sort(key=lambda x: x.get(crit, float("inf")))
    return results[0]

"""
scripts/audit_regime_model.py
=============================
Empirical audit, walk-forward performance evaluation, and model selection
diagnostics for the InvestYo Gaussian HMM regime detector.

Usage:
    python -m scripts.audit_regime_model [--compare] [--json] [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._bootstrap import bootstrap
bootstrap()

import pandas as pd
import numpy as np

from settings import settings
from regime.hmm_regime import build_feature_matrix, HMMRegimeDetector
from validation.regime_diagnostics import (
    compare_model_configurations,
    evaluate_state_performance,
    run_walk_forward_evaluation,
)

logger = logging.getLogger("RegimeAudit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def load_historical_data() -> Tuple[pd.DataFrame, pd.Series, pd.Series, Optional[pd.Series]]:
    """Loads SPY price bars and FRED macro series from SQLite."""
    db_path = settings.LOCAL_DATA_ROOT / "quant_platform.db"
    if not db_path.exists():
        db_path = Path("quant_platform.db")

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}.")

    conn = sqlite3.connect(db_path)
    try:
        spy_df = pd.read_sql(
            "SELECT date, close as Close FROM price_bars WHERE symbol='SPY' ORDER BY date",
            conn,
            parse_dates=["date"],
            index_col="date",
        )
        macro_df = pd.read_sql(
            "SELECT date, series_id, value FROM macro_history "
            "WHERE series_id IN ('VIXCLS', 'T10Y2Y', 'BAMLH0A0HYM2') ORDER BY date",
            conn,
            parse_dates=["date"],
        )
    finally:
        conn.close()

    if spy_df.empty:
        raise ValueError("No SPY price bars found in database.")
    if macro_df.empty:
        raise ValueError("No macro series found in database.")

    pivoted = macro_df.pivot(index="date", columns="series_id", values="value")

    vix_series = pivoted["VIXCLS"] if "VIXCLS" in pivoted.columns else pd.Series(dtype=float)
    t10y2y_series = pivoted["T10Y2Y"] if "T10Y2Y" in pivoted.columns else pd.Series(dtype=float)
    credit_series = pivoted["BAMLH0A0HYM2"] if "BAMLH0A0HYM2" in pivoted.columns else None

    return spy_df, vix_series, t10y2y_series, credit_series


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Gaussian HMM Regime Detector.")
    parser.add_argument("--compare", action="store_true", help="Compare model configurations across states and covariances.")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON diagnostic payload.")
    parser.add_argument("--states", type=int, default=None, help="Override HMM state count (default from settings).")
    parser.add_argument("--cov", type=str, default=None, help="Override covariance type (default from settings).")
    parser.add_argument("--output", type=str, default=None, help="Optional filepath to save diagnostic results.")
    args = parser.parse_args()

    n_states = args.states or settings.HMM_N_STATES
    cov_type = (args.cov or settings.HMM_COVARIANCE_TYPE).lower()

    logger.info(
        "Loading historical data for SPY, VIX, and Macro series (n_states=%d, cov=%s)...",
        n_states, cov_type
    )

    try:
        spy_df, vix_s, t10y2y_s, credit_s = load_historical_data()
    except Exception as e:
        logger.error(f"Failed to load historical data: {e}")
        return 1

    features_df = build_feature_matrix(
        spy_df,
        vix_s,
        t10y2y_s,
        credit_spread_series=credit_s if settings.HMM_CREDIT_SPREAD_FEATURE_ENABLED else None,
        include_vol_term_spread=settings.HMM_VOL_TERM_SPREAD_FEATURE_ENABLED,
    )

    logger.info(
        "Constructed feature matrix with %d rows (%s to %s). Columns: %s",
        len(features_df),
        features_df.index.min().strftime("%Y-%m-%d"),
        features_df.index.max().strftime("%Y-%m-%d"),
        list(features_df.columns),
    )

    # 1. Walk-Forward Evaluation
    logger.info("Running causal walk-forward regime prediction...")
    wf_df = run_walk_forward_evaluation(
        features_df,
        n_states=n_states,
        covariance_type=cov_type,
        retrain_freq_days=settings.HMM_RETRAIN_FREQ_DAYS,
        n_iter=settings.HMM_N_ITER,
        tol=settings.HMM_TOL,
        min_fit_rows=100,
    )

    perf_metrics = evaluate_state_performance(wf_df, return_column="spy_return")

    comparison_results = None
    if args.compare:
        logger.info("Running model comparison grid across states and covariances...")
        comparison_results = compare_model_configurations(
            features_df, state_counts=[2, 3, 4], covariance_types=["diag", "full"]
        )

    logger.info("Computing full-sample model diagnostics...")
    hmm = HMMRegimeDetector(n_states=n_states, covariance_type=cov_type)
    hmm.fit(features_df)
    diagnostics = hmm.compute_diagnostics(features_df)

    output_payload = {
        "dataset_summary": {
            "total_bars": len(features_df),
            "start_date": features_df.index.min().strftime("%Y-%m-%d"),
            "end_date": features_df.index.max().strftime("%Y-%m-%d"),
            "features": list(features_df.columns),
            "walk_forward_days": len(wf_df),
        },
        "configuration": {
            "n_states": n_states,
            "covariance_type": cov_type,
            "retrain_freq_days": settings.HMM_RETRAIN_FREQ_DAYS,
            "n_iter": settings.HMM_N_ITER,
            "tol": settings.HMM_TOL,
        },
        "state_performance": perf_metrics,
        "model_diagnostics": diagnostics,
        "model_comparisons": comparison_results,
    }

    if args.json:
        json_str = json.dumps(output_payload, indent=2)
        if args.output:
            Path(args.output).write_text(json_str)
            logger.info(f"Saved JSON audit to {args.output}")
        else:
            print(json_str)
        return 0

    # Formatted Markdown Reporting
    print("\n" + "=" * 80)
    print("           INVESTYO GAUSSIAN HMM REGIME AUDIT REPORT")
    print("=" * 80)
    print(f"Data Range    : {features_df.index.min().date()} to {features_df.index.max().date()} ({len(features_df)} trading days)")
    print(f"Features      : {', '.join(features_df.columns)}")
    print(f"HMM Config    : {n_states} States | Covariance: {cov_type} | Retrain: {settings.HMM_RETRAIN_FREQ_DAYS}d | EM: {settings.HMM_N_ITER} iters")
    print("-" * 80)

    print("\n### 1. WALK-FORWARD EMPIRICAL REGIME PERFORMANCE")
    print("-" * 80)
    print(f"{'State':<12} {'Count':<8} {'Freq':<8} {'Ann. Ret':<12} {'Ann. Vol':<12} {'Sharpe':<10} {'Sortino':<10} {'Max DD':<10}")
    print("-" * 80)

    for state, data in perf_metrics["state_metrics"].items():
        print(
            f"{state:<12} "
            f"{data['days_count']:<8} "
            f"{data['frequency']:<8.1%} "
            f"{data['annualized_return']:>+8.2%}    "
            f"{data['annualized_volatility']:>8.2%}    "
            f"{data['sharpe_ratio']:>+6.2f}    "
            f"{data['sortino_ratio']:>+6.2f}    "
            f"{data['max_drawdown']:>8.2%}"
        )
    print("-" * 80)

    mono_status = "PASSED" if perf_metrics.get("volatility_monotonicity") else "UNVERIFIED"
    print(f"Volatility Monotonicity Gate (Bull < Sideways < Bear): {mono_status}")

    if comparison_results:
        print("\n### 2. MODEL COMPARISON GRID (AIC / BIC)")
        print("-" * 80)
        print(f"{'Rank':<6} {'States':<8} {'Covariance':<12} {'Params':<8} {'LogLik':<12} {'AIC':<12} {'BIC':<12}")
        print("-" * 80)
        for idx, res in enumerate(comparison_results, 1):
            print(
                f"{idx:<6} "
                f"{res['n_states']:<8} "
                f"{res['covariance_type']:<12} "
                f"{res['n_parameters']:<8} "
                f"{res['log_likelihood']:>10.1f}  "
                f"{res['aic']:>10.1f}  "
                f"{res['bic']:>10.1f}"
            )
        print("-" * 80)

    if args.output:
        Path(args.output).write_text(json.dumps(output_payload, indent=2))
        logger.info(f"Saved audit results to {args.output}")

    print("\n" + "=" * 80 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
